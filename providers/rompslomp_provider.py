"""
rompslomp_provider.py - AccountingProvider voor Rompslomp.

Gebaseerd op de officiele OpenAPI-spec:
  https://app.rompslomp.nl/developer/swagger/v1/swagger.yaml

Belangrijke Rompslomp-specifics die we mappen:
  - Grootboeken heten 'accounts' (niet ledger_accounts)
  - BTW-tarieven heten 'vat_types' (niet vat_codes/tax_codes)
  - Inkoopfacturen heten 'expenses'
  - Prijzen en aantallen zijn STRINGS in JSON (arbitrary precision)
  - Contacten gebruiken is_individual/is_supplier flags
  - 'concept' -> 'published' via trigger _publish: true
  - PDFs worden base64-encoded in attachment_objects[] op het document zelf
    (geen aparte attachments-endpoint)
  - Geen documented /bank_accounts of /financial_statements - bank-flow
    moet via UI of via /payments endpoint, niet via een import-API.
  - LET OP: /contacts negeert zoekparameters; we filteren client-side.
"""

from __future__ import annotations

import base64
import os
import re
import time
from pathlib import Path

import requests

from .base import AccountingProvider, ProviderError


BASE_URL = "https://api.rompslomp.nl/api/v1"


class RompslompProvider(AccountingProvider):
    name = "rompslomp"
    display_name = "Rompslomp"

    def __init__(self, api_token=None, company_id=None):
        self.api_token = api_token or os.environ.get("ROMPSLOMP_API_TOKEN")
        self.company_id = company_id or os.environ.get("ROMPSLOMP_COMPANY_ID")
        if not self.api_token:
            raise ProviderError(
                "ROMPSLOMP_API_TOKEN ontbreekt in .env. "
                "Maak een API-token via 'Mijn account' > 'Mijn API tokens'.",
                provider="rompslomp",
            )
        self._session = requests.Session()
        self._session.headers.update({
            "Authorization": f"Bearer {self.api_token}",
            "Accept": "application/json",
            "Content-Type": "application/json",
        })
        self._accounts_cache = None
        self._vat_types_cache = None
        self._company_info = None
        if not self.company_id:
            self.company_id = self._discover_company_id()

    # ---------- intern ----------
    def _discover_company_id(self):
        try:
            r = self._session.get(f"{BASE_URL}/companies.json", timeout=15)
            if not r.ok:
                raise ProviderError(
                    f"Kon companies niet ophalen (HTTP {r.status_code}). "
                    "Controleer API-token + activatie van API-functie.",
                    provider="rompslomp", status=r.status_code,
                )
            data = r.json()
            companies = data if isinstance(data, list) else data.get("companies", [])
            if not companies:
                raise ProviderError(
                    "Geen administraties onder dit API-token.",
                    provider="rompslomp",
                )
            self._company_info = companies[0]
            cid = companies[0].get("id")
            if len(companies) > 1:
                print(f"[Rompslomp] {len(companies)} administraties. "
                      f"Gebruikt: '{companies[0].get('name')}' (id={cid}).")
            return str(cid)
        except requests.RequestException as e:
            raise ProviderError(f"Rompslomp niet bereikbaar: {e}",
                                provider="rompslomp")

    def _url(self, path):
        return f"{BASE_URL}/companies/{self.company_id}/{path.lstrip('/')}"

    def _request(self, method, path, max_retries=4, **kwargs):
        url = self._url(path)
        for attempt in range(max_retries + 1):
            resp = self._session.request(method, url, timeout=30, **kwargs)
            if resp.status_code == 429 and attempt < max_retries:
                wait = int(resp.headers.get("Retry-After", 2 ** attempt))
                time.sleep(min(wait, 30))
                continue
            if resp.status_code >= 500 and attempt < max_retries:
                time.sleep(2 ** attempt)
                continue
            if not resp.ok:
                raise ProviderError(
                    f"Rompslomp {resp.status_code} bij {url}: {resp.text[:500]}",
                    provider="rompslomp", status=resp.status_code,
                )
            if resp.status_code == 204 or not resp.content:
                return None
            return resp.json()
        raise ProviderError("Rate limit na retries", provider="rompslomp",
                            status=429)

    # ---------- diagnose ----------
    def health_check(self):
        try:
            accs = self._list_accounts(selection="all")
            return {
                "ok": True,
                "provider": self.name,
                "administration_id": self.company_id,
                "administration_name":
                    (self._company_info or {}).get("name") or None,
                "accounts": len(accs),
            }
        except ProviderError:
            raise

    # ---------- contacten ----------
    @staticmethod
    def _norm_naam(s):
        """Normaliseer voor vergelijking: lowercase, punten weg (zodat 'B.V.'
        op 'BV' matcht), komma's naar spatie, spaties samengevoegd."""
        s = (s or "").lower().replace(".", "").replace(",", " ")
        return re.sub(r"\s+", " ", s).strip()

    def find_contact(self, query):
        """Zoek een contact op naam.

        LET OP: Rompslomp negeert zoekparameters (?q=/?query=/...) op het
        /contacts-endpoint en geeft ALTIJD alle contacten terug. We filteren
        daarom client-side op genormaliseerde naam (exacte match > substring).
        Geen match -> None, zodat de aanroeper een nieuw contact aanmaakt.
        """
        if not query or not query.strip():
            return None
        target = self._norm_naam(query)
        if not target:
            return None

        best = None
        page = 1
        while page <= 25:  # tot 2500 contacten; ruim voldoende
            results = self._request(
                "GET", f"contacts.json?per_page=100&page={page}"
            )
            if isinstance(results, dict):
                results = results.get("contacts") or []
            if not results:
                break
            for r in results:
                c = r.get("contact", r) if isinstance(r, dict) else r
                naam = self._norm_naam(
                    c.get("company_name") or c.get("contact_person_name")
                )
                if not naam:
                    continue
                if naam == target:
                    return c  # exacte match wint direct
                # substring alleen bij voldoende lengte (geen 'bv'/'nv'-ruis)
                if best is None and len(naam) >= 4 and len(target) >= 4 \
                        and (target in naam or naam in target):
                    best = c
            if len(results) < 100:
                break
            page += 1
        return best

    def create_contact(self, data):
        """
        Map interne velden -> Rompslomp Contact schema.

        Conventie:
          is_individual=false (default) -> bedrijf
          is_supplier=true  -> leverancier (default false = klant)
        """
        is_individual = not bool(data.get("company_name"))
        rs_data = {
            "is_individual": is_individual,
            "is_supplier": bool(data.get("supplier")),
            "address": data.get("address1"),
            "zipcode": data.get("zipcode"),
            "city": data.get("city"),
            "country_code": (data.get("country") or "NL")[:2].upper(),
            "phone": data.get("phone"),
            "contact_person_email_address": data.get("email"),
            "kvk_number": data.get("chamber_of_commerce"),
            "vat_number": data.get("tax_number"),
        }
        if is_individual:
            rs_data["contact_person_name"] = (
                data.get("firstname", "") + " " + data.get("lastname", "")
            ).strip() or "Onbekend"
        else:
            rs_data["company_name"] = data.get("company_name") or "Onbekend"
        rs_data = {k: v for k, v in rs_data.items() if v not in (None, "")}
        resp = self._request("POST", "contacts.json", json={"contact": rs_data})
        if isinstance(resp, dict) and "contact" in resp:
            return resp["contact"]
        return resp

    # ---------- BTW (vat_types) ----------
    def list_tax_rates(self, tax_rate_type=None):
        """
        Rompslomp heeft 1 lijst van vat_types (geen aparte sales/purchase).
        Vereist verplichte 'selection=all' query parameter.
        Veld 'value' is een decimal string: "0.21" = 21%.
        """
        if self._vat_types_cache is None:
            data = self._request("GET", "vat_types.json?selection=all&per_page=100")
            if isinstance(data, dict):
                data = data.get("vat_types") or []
            self._vat_types_cache = data or []
        return self._vat_types_cache

    def find_or_create_tax_rate(self, percentage, tax_rate_type="purchase_invoice"):
        rates = self.list_tax_rates()
        target = float(percentage) if percentage is not None else 0.0

        def _normalize(r):
            """Voeg 'percentage' veld toe (Moneybird-compat voor boek_agent)."""
            r = dict(r)
            raw = r.get("value")
            try:
                v = float(raw) if raw is not None else None
                if v is not None:
                    r["percentage"] = v * 100 if abs(v) <= 1 else v
            except (TypeError, ValueError):
                pass
            return r

        # Vrijgesteld
        if percentage is None:
            for r in rates:
                if r.get("name") == "vat_none":
                    return _normalize(r)

        for r in rates:
            raw = r.get("value") or r.get("rate") or r.get("percentage")
            if raw is None:
                continue
            try:
                v = float(raw)
                pct = v * 100 if abs(v) <= 1 else v
            except (TypeError, ValueError):
                continue
            # Bij 0% prefereer vat_zero boven vat_reverse_charged
            if abs(pct - target) < 0.5:
                if target == 0 and r.get("name") == "vat_reverse_charged":
                    # Onthoud, maar zoek liever vat_zero
                    continue
                return _normalize(r)
        # Tweede ronde: nu wel vat_reverse_charged accepteren voor 0%
        if target == 0:
            for r in rates:
                if r.get("name") == "vat_zero":
                    return _normalize(r)
            for r in rates:
                if r.get("name") in ("vat_reverse_charged", "vat_none"):
                    return _normalize(r)
        if rates:
            return _normalize(rates[0])
        raise ProviderError(
            "Geen BTW-types gevonden in Rompslomp.",
            provider="rompslomp",
        )

    # ---------- grootboeken (accounts) ----------
    def _list_accounts(self, selection="all"):
        """selection is verplicht: all|ledger|payment|revenue|costs."""
        if not hasattr(self, "_accounts_caches"):
            self._accounts_caches = {}
        if selection not in self._accounts_caches:
            data = self._request(
                "GET", f"accounts.json?selection={selection}&per_page=100"
            )
            if isinstance(data, dict):
                data = data.get("accounts") or []
            self._accounts_caches[selection] = data or []
        # Behoud back-compat met self._accounts_cache (gebruikt in health_check)
        self._accounts_cache = self._accounts_caches.get("all", [])
        return self._accounts_caches[selection]

    def purchase_ledgers(self):
        # Rompslomp heeft een 'costs' selectie die direct de juiste lijst geeft
        return self._list_accounts(selection="costs")

    def sales_ledgers(self):
        return self._list_accounts(selection="revenue")

    # ---------- helper: regels mappen ----------
    def _map_invoice_lines(self, details_attributes):
        lines = []
        for d in details_attributes or []:
            qty = _parse_qty(d.get("amount", "1 x"))
            price = float(d.get("price", 0) or 0)
            line = {
                "description": d.get("description") or "Regel",
                "price_per_unit": f"{price:.2f}",
                "quantity": f"{qty:.2f}",
            }
            if d.get("tax_rate_id"):
                line["vat_type_id"] = int(d["tax_rate_id"])
            if d.get("ledger_account_id"):
                line["account_id"] = int(d["ledger_account_id"])
            lines.append(line)
        return lines

    # ---------- inkoopfacturen (expenses) ----------
    def create_purchase_invoice(self, payload):
        """
        Rompslomp POST /expenses accepteert:
          - date, state, type_account_id, currency, contact_id, invoice_lines
        Direct publiceren via state='published'.
        Het grootboek staat op factuur-niveau (type_account_id).

        Factuurnummer: het Expense-model markeert invoice_number als 'read only',
        maar het aanmaak-endpoint is niet gedocumenteerd. We proberen het nummer
        daarom mee te sturen; weigert Rompslomp dat, dan boeken we alsnog zonder
        (de boeking mag nooit op het factuurnummer stuklopen).
        """
        details = payload.get("details_attributes", [])
        # Factuur-grootboek: pak het account_id uit de eerste regel als default
        type_account_id = None
        if details:
            try:
                type_account_id = int(details[0].get("ledger_account_id"))
            except (TypeError, ValueError):
                type_account_id = None
        if not type_account_id:
            # Eerste kosten-grootboek als fallback
            costs = self.purchase_ledgers()
            if costs:
                type_account_id = costs[0]["id"]

        rs_payload = {
            "contact_id": int(payload["contact_id"]) if payload.get("contact_id") else None,
            "date": payload.get("date"),
            "state": "published",
            "type_account_id": type_account_id,
            "currency": (payload.get("currency") or "EUR").lower(),
            "invoice_lines": self._map_invoice_lines(details),
        }
        rs_payload = {k: v for k, v in rs_payload.items() if v is not None}

        # Factuurnummer van de leverancier meegeven (best effort).
        factuurnr = payload.get("reference") or payload.get("factuurnummer")
        if factuurnr:
            rs_payload["invoice_number"] = str(factuurnr)

        try:
            resp = self._request("POST", "expenses.json", json={"expense": rs_payload})
        except ProviderError:
            # Mogelijk weigert Rompslomp invoice_number (read-only). Opnieuw zonder.
            if "invoice_number" in rs_payload:
                rs_payload.pop("invoice_number", None)
                resp = self._request("POST", "expenses.json", json={"expense": rs_payload})
            else:
                raise
        if isinstance(resp, dict) and "expense" in resp:
            return resp["expense"]
        return resp

    def delete_purchase_invoice(self, document_id):
        """Verwijder een inkoopfactuur (expense). Gebruikt door de sandbox-
        opruimfunctie. Retourneert True bij succes. Rompslomp: DELETE
        /expenses/{id}.json (geeft meestal 204)."""
        self._request("DELETE", f"expenses/{document_id}.json")
        return True

    def list_purchase_invoices(self, state="published", per_page=100, page=1):
        """Lijst geboekte inkoopfacturen (expenses). Gebruikt door de
        ijk-import om uit de boekhistorie te leren. 'state' kan published
        of all zijn."""
        data = self._request(
            "GET", f"expenses.json?state={state}&per_page={per_page}&page={page}"
        )
        if isinstance(data, dict):
            data = data.get("expenses") or []
        return data or []

    def get_purchase_invoice(self, document_id):
        """Haal één expense met regel-details op (omschrijving +
        ledger_account_id per regel). De lijst-respons bevat niet altijd de
        regels, dus de ijk-import haalt per factuur het detail op."""
        data = self._request("GET", f"expenses/{document_id}.json")
        if isinstance(data, dict) and "expense" in data:
            return data["expense"]
        return data

    def attach_pdf_purchase(self, document_id, pdf_path):
        """Rompslomp ondersteunt GEEN PDF-bijlagen aan expenses via API.
        Alleen sales_invoices hebben een attachments-endpoint."""
        raise ProviderError(
            "Rompslomp ondersteunt geen PDF-bijlagen aan inkoopfacturen via "
            "de API. Voeg de bijlage handmatig toe in de Rompslomp-app, of "
            "gebruik de Magic Shoebox-flow.",
            provider="rompslomp",
        )

    # ---------- verkoopfacturen ----------
    def create_sales_invoice(self, payload):
        rs_payload = {
            "contact_id": int(payload["contact_id"]) if payload.get("contact_id") else None,
            "date": payload.get("invoice_date") or payload.get("date"),
            "due_date": payload.get("due_date"),
            "currency": (payload.get("currency") or "EUR").lower(),
            "invoice_lines": self._map_invoice_lines(
                payload.get("details_attributes", [])
            ),
        }
        rs_payload = {k: v for k, v in rs_payload.items() if v is not None}
        resp = self._request(
            "POST", "sales_invoices.json", json={"sales_invoice": rs_payload}
        )
        if isinstance(resp, dict) and "sales_invoice" in resp:
            return resp["sales_invoice"]
        return resp

    def send_sales_invoice(self, sales_invoice_id, delivery_method="Manual"):
        # Trigger _publish op de verkoopfactuur
        return self._request(
            "PATCH",
            f"sales_invoices/{sales_invoice_id}.json",
            json={"sales_invoice": {"_publish": True}},
        )

    def attach_pdf_sales(self, sales_invoice_id, pdf_path):
        """POST /sales_invoices/{id}/attachments met base64-encoded body."""
        p = Path(pdf_path)
        with p.open("rb") as f:
            b64 = base64.b64encode(f.read()).decode("ascii")
        body = {
            "attachment_object": {
                "attachment": b64,
                "attachment_file_name": p.name,
            }
        }
        return self._request(
            "POST",
            f"sales_invoices/{sales_invoice_id}/attachments.json",
            json=body,
        )

    # ---------- bank ----------
    def primary_financial_account(self):
        # Rompslomp heeft geen documented /financial_accounts endpoint.
        # Bank-functie wordt door Rompslomp UI-zijds afgehandeld.
        raise ProviderError(
            "Bank-import is in Rompslomp niet via API beschikbaar. "
            "Gebruik de Rompslomp-app voor bankafschriften.",
            provider="rompslomp",
        )

    def create_financial_statement(self, financial_account_id, reference,
                                    mutations, **kwargs):
        raise ProviderError(
            "Bankafschrift-import wordt door Rompslomp niet ondersteund via API.",
            provider="rompslomp",
        )

    def link_mutation_to_booking(self, mutation_id, booking_type,
                                  booking_id, price):
        raise ProviderError(
            "Mutatie-koppeling wordt door Rompslomp niet ondersteund via API.",
            provider="rompslomp",
        )

    def list_open_purchase_invoices(self):
        try:
            data = self._request(
                "GET", "expenses.json?state=published&per_page=100"
            )
            if isinstance(data, dict):
                data = data.get("expenses") or []
            return data or []
        except ProviderError:
            return []

    def list_open_sales_invoices(self):
        try:
            data = self._request(
                "GET", "sales_invoices.json?state=published&per_page=100"
            )
            if isinstance(data, dict):
                data = data.get("sales_invoices") or []
            return data or []
        except ProviderError:
            return []

    # ---------- UI-link ----------
    def document_url(self, document_id, doc_type="purchase_invoice"):
        if doc_type == "sales_invoice":
            return f"https://app.rompslomp.nl/companies/{self.company_id}/sales_invoices/{document_id}"
        return f"https://app.rompslomp.nl/companies/{self.company_id}/expenses/{document_id}"


def _parse_qty(amount_str):
    if isinstance(amount_str, (int, float)):
        return float(amount_str)
    s = str(amount_str).strip()
    try:
        return float(s.split()[0].replace(",", "."))
    except (ValueError, IndexError):
        return 1.0
