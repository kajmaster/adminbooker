"""
exact_provider.py - AccountingProvider-skeleton voor Exact Online.

LET OP: dit is een skeleton. Methodes raisen ProviderError met "nog niet
geimplementeerd" totdat de echte koppeling is uitgewerkt.

Wat er nog moet gebeuren voor een werkende koppeling:

  1. OAuth 2.0 authorization flow:
     - Gebruiker authoriseert via https://start.exactonline.nl/api/oauth2/auth
       met response_type=code en client_id, redirect_uri.
     - Wij krijgen een 'code' terug.
     - Wissel de code in voor access_token + refresh_token via
       POST https://start.exactonline.nl/api/oauth2/token.
     - Sla access_token op (geldig 10 min), refresh_token (langer geldig).
     - Bij elke API-call: stuur access_token. Bij 401: refresh.

  2. Division-ID ophalen:
     - GET /api/v1/current/Me  -> CurrentDivision
     - Of /api/v1/<division>/system/Divisions -> alle divisies

  3. Endpoints voor onze functies:
     - Contacten:        /api/v1/{division}/crm/Accounts
     - Inkoopfacturen:   /api/v1/{division}/purchaseentry/PurchaseEntries
     - Verkoopfacturen:  /api/v1/{division}/salesinvoice/SalesInvoices
     - Grootboeken:      /api/v1/{division}/financial/GLAccounts
     - BTW codes:        /api/v1/{division}/vat/VATCodes
     - Bank:             /api/v1/{division}/financialtransaction/TransactionLines

  4. Rate limiting:
     - Exact heeft een API-limiet per minuut, respecteer X-RateLimit-Remaining
       en X-RateLimit-Reset headers.

  5. Data-mapping:
     - Onze interne payload-structuur (zoals boek_agent gebruikt) moet
       worden vertaald naar Exact's veldnamen (PascalCase).

Configuratie via .env:
    EXACT_CLIENT_ID=...
    EXACT_CLIENT_SECRET=...
    EXACT_REDIRECT_URI=http://localhost:5000/oauth/exact/callback
    EXACT_DIVISION=...            # optioneel, anders ophalen uit Me
    EXACT_REFRESH_TOKEN=...       # opgeslagen na eerste autorisatie
"""

from __future__ import annotations

import os

from .base import AccountingProvider, ProviderError


_NIET_GEREED = (
    "Exact Online-koppeling is nog niet geimplementeerd. "
    "Configureer EXACT_CLIENT_ID + EXACT_CLIENT_SECRET in .env, autoriseer "
    "via /oauth/exact/start in de browser, en implementeer de Exact-endpoints "
    "in providers/exact_provider.py."
)


class ExactOnlineProvider(AccountingProvider):
    name = "exact"
    display_name = "Exact Online"

    def __init__(self):
        self.client_id = os.environ.get("EXACT_CLIENT_ID")
        self.client_secret = os.environ.get("EXACT_CLIENT_SECRET")
        self.redirect_uri = os.environ.get(
            "EXACT_REDIRECT_URI", "http://localhost:5000/oauth/exact/callback"
        )
        self.division = os.environ.get("EXACT_DIVISION")
        self.refresh_token = os.environ.get("EXACT_REFRESH_TOKEN")
        self.access_token = None

        if not self.client_id or not self.client_secret:
            raise ProviderError(
                "EXACT_CLIENT_ID en EXACT_CLIENT_SECRET ontbreken in .env. "
                "Maak een app aan op apps.exactonline.com (developer portal).",
                provider="exact",
            )

    # ---------- TODO: OAuth flow ----------
    def authorization_url(self):
        """URL waar de gebruiker naartoe moet om te autoriseren."""
        from urllib.parse import urlencode
        params = {
            "client_id": self.client_id,
            "redirect_uri": self.redirect_uri,
            "response_type": "code",
            "force_login": "0",
        }
        return "https://start.exactonline.nl/api/oauth2/auth?" + urlencode(params)

    def exchange_code(self, code):
        """Wissel autorisatie-code in voor access+refresh token."""
        raise ProviderError(_NIET_GEREED, provider="exact")

    def refresh_access_token(self):
        """Vernieuw de access_token met de refresh_token."""
        raise ProviderError(_NIET_GEREED, provider="exact")

    # ---------- alle interface-methods, nog niet geimplementeerd ----------
    def health_check(self):
        return {
            "ok": False,
            "provider": self.name,
            "error": "Skeleton - implementeer OAuth + API-calls",
            "authorize_url": self.authorization_url(),
        }

    def find_contact(self, query):
        raise ProviderError(_NIET_GEREED, provider="exact")

    def create_contact(self, data):
        raise ProviderError(_NIET_GEREED, provider="exact")

    def find_or_create_tax_rate(self, percentage, tax_rate_type="purchase_invoice"):
        raise ProviderError(_NIET_GEREED, provider="exact")

    def purchase_ledgers(self):
        raise ProviderError(_NIET_GEREED, provider="exact")

    def sales_ledgers(self):
        raise ProviderError(_NIET_GEREED, provider="exact")

    def create_purchase_invoice(self, payload):
        raise ProviderError(_NIET_GEREED, provider="exact")

    def attach_pdf_purchase(self, document_id, pdf_path):
        raise ProviderError(_NIET_GEREED, provider="exact")

    def create_sales_invoice(self, payload):
        raise ProviderError(_NIET_GEREED, provider="exact")

    def send_sales_invoice(self, sales_invoice_id, delivery_method="Manual"):
        raise ProviderError(_NIET_GEREED, provider="exact")

    def attach_pdf_sales(self, sales_invoice_id, pdf_path):
        raise ProviderError(_NIET_GEREED, provider="exact")

    def primary_financial_account(self):
        raise ProviderError(_NIET_GEREED, provider="exact")

    def create_financial_statement(self, financial_account_id, reference, mutations, **kwargs):
        raise ProviderError(_NIET_GEREED, provider="exact")

    def link_mutation_to_booking(self, mutation_id, booking_type, booking_id, price):
        raise ProviderError(_NIET_GEREED, provider="exact")

    def list_open_purchase_invoices(self):
        raise ProviderError(_NIET_GEREED, provider="exact")

    def list_open_sales_invoices(self):
        raise ProviderError(_NIET_GEREED, provider="exact")

    def document_url(self, document_id, doc_type="purchase_invoice"):
        if not self.division:
            return ""
        if doc_type == "sales_invoice":
            return f"https://start.exactonline.nl/docs/HrmSalesInvoice.aspx?Division={self.division}&InvoiceID={document_id}"
        return f"https://start.exactonline.nl/docs/PurchaseEntry.aspx?Division={self.division}&PurchaseEntryID={document_id}"
