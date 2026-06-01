"""
moneybird_provider.py - AccountingProvider-implementatie voor Moneybird.

Wraps de bestaande Moneybird-client (moneybird.py in de root) zodat de rest
van AdminBooker er via de abstracte interface mee praat.
"""

from __future__ import annotations

import os
from pathlib import Path

# We gebruiken de bestaande moneybird.py in de root
import sys
_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

import moneybird as _mb_module
from moneybird import Moneybird, MoneybirdError

from .base import AccountingProvider, ProviderError


class MoneybirdProvider(AccountingProvider):
    name = "moneybird"
    display_name = "Moneybird"

    def __init__(self, api_key=None, administration_id=None):
        key = api_key or os.environ.get("MONEYBIRD_API_KEY")
        admin = administration_id or os.environ.get("MONEYBIRD_ADMINISTRATION_ID")
        if not key or not admin:
            raise ProviderError(
                "Moneybird API_KEY of ADMINISTRATION_ID ontbreekt in .env",
                provider="moneybird",
            )
        self._client = Moneybird(key, admin)

    # ---------- diagnose ----------
    def health_check(self):
        try:
            ledgers = self._client.list_ledger_accounts()
            # haal admin-naam apart op via directe call
            import requests
            r = requests.get(
                "https://moneybird.com/api/v2/administrations.json",
                headers={
                    "Authorization": f"Bearer {self._client.api_key}",
                    "Accept": "application/json",
                },
                timeout=10,
            )
            admin_name = None
            if r.ok:
                for a in r.json():
                    if a.get("id") == self._client.admin_id:
                        admin_name = a.get("name")
                        break
            return {
                "ok": True,
                "provider": self.name,
                "administration_id": self._client.admin_id,
                "administration_name": admin_name,
                "ledger_count": len(ledgers),
            }
        except MoneybirdError as e:
            raise ProviderError(str(e), provider="moneybird", status=e.status)

    # ---------- contacten ----------
    def find_contact(self, query):
        try:
            return self._client.find_contact(query)
        except MoneybirdError as e:
            raise ProviderError(str(e), provider="moneybird", status=e.status)

    def create_contact(self, data):
        try:
            return self._client.create_contact(data)
        except MoneybirdError as e:
            raise ProviderError(str(e), provider="moneybird", status=e.status)

    # ---------- tax ----------
    def find_or_create_tax_rate(self, percentage, tax_rate_type="purchase_invoice"):
        try:
            return self._client.find_or_create_tax_rate(percentage, tax_rate_type)
        except MoneybirdError as e:
            raise ProviderError(str(e), provider="moneybird", status=e.status)

    # ---------- grootboeken ----------
    def purchase_ledgers(self):
        return self._client.purchase_ledgers()

    def sales_ledgers(self):
        return self._client.sales_ledgers()

    # ---------- inkoop ----------
    def create_purchase_invoice(self, payload):
        try:
            return self._client.create_purchase_invoice(payload)
        except MoneybirdError as e:
            raise ProviderError(str(e), provider="moneybird", status=e.status)

    def attach_pdf_purchase(self, document_id, pdf_path):
        try:
            return self._client.attach_pdf(document_id, pdf_path)
        except MoneybirdError as e:
            raise ProviderError(str(e), provider="moneybird", status=e.status)

    # ---------- verkoop ----------
    def create_sales_invoice(self, payload):
        try:
            return self._client.create_sales_invoice(payload)
        except MoneybirdError as e:
            raise ProviderError(str(e), provider="moneybird", status=e.status)

    def send_sales_invoice(self, sales_invoice_id, delivery_method="Manual"):
        try:
            return self._client.send_sales_invoice(sales_invoice_id, delivery_method)
        except MoneybirdError as e:
            raise ProviderError(str(e), provider="moneybird", status=e.status)

    def attach_pdf_sales(self, sales_invoice_id, pdf_path):
        try:
            return self._client.attach_pdf_sales(sales_invoice_id, pdf_path)
        except MoneybirdError as e:
            raise ProviderError(str(e), provider="moneybird", status=e.status)

    # ---------- bank ----------
    def primary_financial_account(self):
        return self._client.primary_financial_account()

    def create_financial_statement(
        self, financial_account_id, reference, mutations, **kwargs
    ):
        try:
            return self._client.create_financial_statement(
                financial_account_id=financial_account_id,
                reference=reference,
                mutations=mutations,
                **kwargs,
            )
        except MoneybirdError as e:
            raise ProviderError(str(e), provider="moneybird", status=e.status)

    def link_mutation_to_booking(self, mutation_id, booking_type, booking_id, price):
        try:
            return self._client.link_mutation_to_booking(
                mutation_id, booking_type, booking_id, price
            )
        except MoneybirdError as e:
            raise ProviderError(str(e), provider="moneybird", status=e.status)

    def list_open_purchase_invoices(self):
        return self._client.list_open_purchase_invoices()

    def list_open_sales_invoices(self):
        return self._client.list_open_sales_invoices()

    # ---------- URLs ----------
    def document_url(self, document_id, doc_type="purchase_invoice"):
        admin = self._client.admin_id
        if doc_type == "sales_invoice":
            return f"https://moneybird.com/{admin}/sales_invoices/{document_id}"
        return f"https://moneybird.com/{admin}/documents/{document_id}"
