"""
base.py - abstract interface voor een boekhoudpakket-koppeling.

Elke concrete provider (Moneybird, Exact Online, Twinfield, etc.) implementeert
deze methods. Zo werkt de rest van AdminBooker hetzelfde, ongeacht het pakket.

Conventie: methods die in een pakket niet bestaan moeten ProviderError raisen
(geen NotImplementedError - dat zou een interne bug suggereren).
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any


class ProviderError(RuntimeError):
    """Raised wanneer de provider iets niet kan / niet ondersteunt."""

    def __init__(self, message, *, provider=None, status=None):
        super().__init__(message)
        self.provider = provider
        self.status = status


class AccountingProvider(ABC):
    """
    Interface voor een boekhoudpakket. Implementeer deze methods om
    AdminBooker met een nieuw pakket te laten praten.

    De rest van AdminBooker werkt alleen met dit type - nooit direct
    met een specifieke API-client.
    """

    name: str = "base"
    display_name: str = "Onbekend pakket"

    # ---------- diagnose ----------
    @abstractmethod
    def health_check(self) -> dict:
        """Test of de verbinding werkt. Retourneer dict met ok/admin_name."""

    # ---------- contacten ----------
    @abstractmethod
    def find_contact(self, query: str) -> dict | None:
        """Zoek een contact op (bedrijfs)naam. None als niet gevonden."""

    @abstractmethod
    def create_contact(self, data: dict) -> dict:
        """Maak een contact aan en geef de aangemaakte record terug."""

    # ---------- tax / btw ----------
    @abstractmethod
    def find_or_create_tax_rate(
        self, percentage: float | None, tax_rate_type: str = "purchase_invoice"
    ) -> dict:
        """Vind of fabriceer een BTW-tarief. Type: 'purchase_invoice'|'sales_invoice'."""

    # ---------- grootboeken ----------
    @abstractmethod
    def purchase_ledgers(self) -> list[dict]:
        """Grootboekrekeningen waar inkoopfacturen op mogen."""

    @abstractmethod
    def sales_ledgers(self) -> list[dict]:
        """Grootboekrekeningen waar verkoopfacturen op mogen."""

    # ---------- inkoopfacturen ----------
    @abstractmethod
    def create_purchase_invoice(self, payload: dict) -> dict:
        """Maak inkoopfactuur aan. Payload structuur zoals boek_agent levert."""

    @abstractmethod
    def attach_pdf_purchase(self, document_id: str, pdf_path: str) -> Any:
        """Hang PDF als bijlage aan een inkoopfactuur."""

    # ---------- verkoopfacturen ----------
    @abstractmethod
    def create_sales_invoice(self, payload: dict) -> dict:
        """Maak verkoopfactuur aan (concept)."""

    @abstractmethod
    def send_sales_invoice(self, sales_invoice_id: str, delivery_method: str = "Manual") -> Any:
        """Markeer verkoopfactuur als verzonden -> status 'open'."""

    @abstractmethod
    def attach_pdf_sales(self, sales_invoice_id: str, pdf_path: str) -> Any:
        """Hang PDF als bijlage aan een verkoopfactuur."""

    # ---------- bank ----------
    @abstractmethod
    def primary_financial_account(self) -> dict | None:
        """Pak primaire bankrekening (object met id, name, iban)."""

    @abstractmethod
    def create_financial_statement(
        self,
        financial_account_id: str,
        reference: str,
        mutations: list[dict],
        **kwargs,
    ) -> dict:
        """Importeer bankafschrift met mutaties."""

    @abstractmethod
    def link_mutation_to_booking(
        self, mutation_id: str, booking_type: str, booking_id: str, price: float
    ) -> Any:
        """Koppel een banktransactie aan een (in/ver)koopfactuur."""

    @abstractmethod
    def list_open_purchase_invoices(self) -> list[dict]:
        """Openstaande inkoopfacturen (state open/late)."""

    @abstractmethod
    def list_open_sales_invoices(self) -> list[dict]:
        """Openstaande verkoopfacturen (state open/late)."""

    # ---------- URLs voor UI ----------
    def document_url(self, document_id: str, doc_type: str = "purchase_invoice") -> str:
        """URL naar de factuur in de web-UI van het pakket. Override per provider."""
        return ""
