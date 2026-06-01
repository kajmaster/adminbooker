"""
Moneybird API client voor AdminBooker.

Lichte client met:
  - Caching van ledger_accounts en tax_rates (per client-instantie)
  - Singleton pattern via from_env()
  - Auto-retry op 429 (rate limit) met exponentiele backoff +
    respect voor Retry-After header
"""

from __future__ import annotations

import os
import time
from pathlib import Path
from typing import Any

import requests


BASE_URL = "https://moneybird.com/api/v2"


class MoneybirdError(RuntimeError):
    def __init__(self, status, body, url):
        super().__init__(f"Moneybird {status} bij {url}: {body}")
        self.status = status
        self.body = body
        self.url = url


class Moneybird:
    def __init__(self, api_key, administration_id):
        if not api_key or not administration_id:
            raise ValueError("api_key en administration_id zijn verplicht")
        self.api_key = api_key
        self.admin_id = administration_id
        self._session = requests.Session()
        self._session.headers.update({
            "Authorization": f"Bearer {api_key}",
            "Accept": "application/json",
        })
        # Caches (per client-instantie)
        self._ledgers_cache = None
        self._tax_rates_cache = None

    # ---------- intern ----------
    def _url(self, path):
        return f"{BASE_URL}/{self.admin_id}/{path.lstrip('/')}"

    def _request(self, method, path, max_retries=4, **kwargs):
        url = self._url(path)
        for attempt in range(max_retries + 1):
            resp = self._session.request(method, url, timeout=30, **kwargs)

            # 429 = rate limit, retry met backoff
            if resp.status_code == 429 and attempt < max_retries:
                # Respect Retry-After als die er is, anders exponentieel
                retry_after_hdr = resp.headers.get("Retry-After")
                wait = 2 ** attempt  # 1, 2, 4, 8 sec
                if retry_after_hdr:
                    try:
                        wait = max(wait, int(retry_after_hdr))
                    except (TypeError, ValueError):
                        pass
                # cap op 30 sec voor we te lang blijven hangen
                wait = min(wait, 30)
                print(f"[Moneybird] 429 rate limit. Wacht {wait}s en probeer opnieuw "
                      f"(poging {attempt + 1}/{max_retries})")
                time.sleep(wait)
                continue

            # 5xx server error - ook retry
            if resp.status_code >= 500 and attempt < max_retries:
                wait = 2 ** attempt
                time.sleep(wait)
                continue

            if not resp.ok:
                raise MoneybirdError(resp.status_code, resp.text, url)
            if resp.status_code == 204 or not resp.content:
                return None
            return resp.json()
        # ge-exhaust met retries
        raise MoneybirdError(429, "Rate limit bereikt na retries", url)

    # ---------- contacten ----------
    def find_contact(self, query):
        if not query or not query.strip():
            return None
        results = self._request(
            "GET", f"contacts.json?query={requests.utils.quote(query.strip())}"
        )
        return results[0] if results else None

    def create_contact(self, data):
        # Bewaak: Moneybird vereist ten minste een naam
        if not (data.get("company_name") or data.get("firstname") or data.get("lastname")):
            data = dict(data)
            data["company_name"] = "Onbekende leverancier"
        return self._request("POST", "contacts.json", json={"contact": data})

    # ---------- tax rates ----------
    def list_tax_rates(self, tax_rate_type=None):
        if self._tax_rates_cache is None:
            self._tax_rates_cache = self._request("GET", "tax_rates.json")
        rates = self._tax_rates_cache
        if tax_rate_type:
            rates = [r for r in rates if r.get("tax_rate_type") == tax_rate_type]
        return rates

    def find_or_create_tax_rate(self, percentage, tax_rate_type="purchase_invoice"):
        """
        Vind een bestaand tax rate. Moneybird API laat aanmaken niet toe -
        dus bij geen match valt deze methode terug op 'vrijgesteld' van het
        juiste type.
        """
        rates = self.list_tax_rates(tax_rate_type=tax_rate_type)

        def _matches(r, pct):
            r_pct = r.get("percentage")
            if pct is None:
                return r_pct in (None,)
            try:
                return float(r_pct) == float(pct)
            except (TypeError, ValueError):
                return False

        for r in rates:
            if _matches(r, percentage) and r.get("active"):
                return r
        # fallback: vrijgesteld van juiste type
        for r in rates:
            if r.get("active") and r.get("percentage") in (None,):
                return r
        raise MoneybirdError(
            422,
            f"Geen bruikbaar tax rate gevonden voor {percentage}% ({tax_rate_type}).",
            "tax_rates.json",
        )

    # ---------- ledger accounts ----------
    def list_ledger_accounts(self):
        if self._ledgers_cache is None:
            self._ledgers_cache = self._request("GET", "ledger_accounts.json")
        return self._ledgers_cache

    def purchase_ledgers(self):
        return [
            a for a in self.list_ledger_accounts()
            if "purchase_invoice" in a.get("allowed_document_types", [])
            and a.get("active")
        ]

    def sales_ledgers(self):
        return [
            a for a in self.list_ledger_accounts()
            if "sales_invoice" in a.get("allowed_document_types", [])
            and a.get("active")
        ]

    # ---------- purchase invoices ----------
    def create_purchase_invoice(self, payload):
        return self._request(
            "POST",
            "documents/purchase_invoices.json",
            json={"purchase_invoice": payload},
        )

    def attach_pdf(self, document_id, pdf_path):
        return self._upload_pdf(
            f"documents/purchase_invoices/{document_id}/attachments.json",
            pdf_path,
        )

    # ---------- sales invoices ----------
    def create_sales_invoice(self, payload):
        return self._request(
            "POST",
            "sales_invoices.json",
            json={"sales_invoice": payload},
        )

    def send_sales_invoice(self, sales_invoice_id, delivery_method="Manual"):
        """
        Markeer concept-verkoopfactuur als verzonden, zodat 'ie van 'draft'
        naar 'open' gaat. delivery_method='Manual' verzendt geen mail.
        """
        return self._request(
            "PATCH",
            f"sales_invoices/{sales_invoice_id}/send_invoice.json",
            json={"sales_invoice_sending": {"delivery_method": delivery_method}},
        )

    def attach_pdf_sales(self, sales_invoice_id, pdf_path):
        return self._upload_pdf(
            f"sales_invoices/{sales_invoice_id}/attachments.json",
            pdf_path,
        )

    # ---------- bank ----------
    def list_financial_accounts(self):
        return self._request("GET", "financial_accounts.json")

    def primary_financial_account(self):
        """Pak eerste actieve bankrekening, of None."""
        accts = self.list_financial_accounts()
        for a in accts:
            if a.get("type") == "FinancialAccount::BankAccount":
                return a
        return accts[0] if accts else None

    def create_financial_statement(self, financial_account_id, reference,
                                    mutations, official_date=None,
                                    official_balance=None):
        """
        Importeer een bankafschrift met mutations.
        mutations: list of dicts with keys date, amount, message,
                   optional contra_account_iban, contra_account_name.
        """
        mutaties_payload = []
        for m in mutations:
            mut = {
                "date": m["date"],
                "amount": f"{m['amount']:.2f}" if isinstance(m["amount"], (int, float)) else str(m["amount"]),
                "message": m.get("message", ""),
            }
            if m.get("contra_account_iban"):
                mut["contra_account"] = m["contra_account_iban"]
            if m.get("contra_account_name"):
                mut["contra_account_name"] = m["contra_account_name"]
            mutaties_payload.append(mut)

        body = {
            "financial_statement": {
                "financial_account_id": str(financial_account_id),
                "reference": reference,
                "financial_mutations_attributes": mutaties_payload,
            }
        }
        if official_date:
            body["financial_statement"]["official_date"] = official_date
        if official_balance is not None:
            body["financial_statement"]["official_balance"] = f"{official_balance:.2f}"
        return self._request("POST", "financial_statements.json", json=body)

    def list_financial_mutations(self, filter_unmatched=False):
        path = "financial_mutations.json"
        if filter_unmatched:
            path += "?filter=fully_matched:false"
        return self._request("GET", path)

    def link_mutation_to_booking(self, mutation_id, booking_type,
                                  booking_id, price):
        """
        booking_type: 'Document' (voor purchase/sales invoice),
                      'ExternalSalesInvoice', etc.
        booking_id: ID van het document
        price: bedrag (positief)
        """
        body = {
            "booking_type": booking_type,
            "booking_id": str(booking_id),
            "price": f"{abs(price):.2f}",
            "description": "AdminBooker automatische match",
        }
        return self._request(
            "PATCH",
            f"financial_mutations/{mutation_id}/link_booking.json",
            json=body,
        )

    def list_open_purchase_invoices(self):
        return self._request(
            "GET",
            "documents/purchase_invoices.json?filter=state:open|late",
        ) or []

    def list_open_sales_invoices(self):
        return self._request(
            "GET",
            "sales_invoices.json?filter=state:open|late",
        ) or []

    # ---------- intern: PDF upload met retry ----------
    def _upload_pdf(self, path, pdf_path):
        path_obj = Path(pdf_path)
        url = self._url(path)
        with path_obj.open("rb") as f:
            files = {"file": (path_obj.name, f, "application/pdf")}
            for attempt in range(4):
                resp = self._session.post(url, files=files, timeout=60)
                if resp.status_code == 429 and attempt < 3:
                    wait = min(2 ** attempt, 30)
                    retry_after = resp.headers.get("Retry-After")
                    if retry_after:
                        try:
                            wait = max(wait, int(retry_after))
                        except (TypeError, ValueError):
                            pass
                    time.sleep(wait)
                    f.seek(0)
                    continue
                if not resp.ok:
                    raise MoneybirdError(resp.status_code, resp.text, url)
                return resp.json() if resp.content else None
        raise MoneybirdError(429, "Rate limit bij PDF-upload", url)


# ---------- singleton via from_env() ----------
_instance: Moneybird | None = None


def from_env(force_reload=False) -> Moneybird:
    """Bouw (of geef terug) een gedeelde Moneybird-client uit .env."""
    global _instance
    if _instance is not None and not force_reload:
        return _instance

    here = Path(__file__).resolve().parent
    env_path = here / ".env"
    if env_path.exists():
        for line in env_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip())

    api_key = os.environ.get("MONEYBIRD_API_KEY")
    admin_id = os.environ.get("MONEYBIRD_ADMINISTRATION_ID")
    if not api_key or not admin_id:
        raise RuntimeError(
            "Stel MONEYBIRD_API_KEY en MONEYBIRD_ADMINISTRATION_ID in via .env"
        )
    _instance = Moneybird(api_key, admin_id)
    return _instance
