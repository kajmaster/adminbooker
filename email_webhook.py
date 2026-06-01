"""
email_webhook.py - Inbound email via Mailgun.

Bouwbedrijven sturen inkoopfacturen door naar hun AdminBooker-adres.
Mailgun pikt het op en POST het naar /webhook/email. Wij halen de
PDF-bijlagen eruit en sturen ze door de bestaande parse → boek → inbox
pipeline — exact hetzelfde als handmatig uploaden, maar volledig automatisch.

Setup (eenmalig):
    1. Maak een Mailgun account aan op mailgun.com
    2. Voeg een domein toe (of gebruik de sandbox voor testen)
    3. Ga naar Receiving → Create Route → Forward naar:
           https://jouw-app.onrender.com/webhook/email
    4. Zet MAILGUN_SIGNING_KEY in je .env (staat in Mailgun dashboard
       onder Settings → Webhooks → HTTP webhook signing key)

Omgevingsvariabelen:
    MAILGUN_SIGNING_KEY   verplicht in productie (leeg = controle uitgeschakeld)
    EMAIL_BOEKING_TYPE    'inkoop' (default) of 'verkoop'
"""
from __future__ import annotations

import hashlib
import hmac
import os
import traceback
from pathlib import Path

from flask import Blueprint, jsonify, request

from boek_agent import boek, boek_verkoop
from pdf_parser import parse_pdf
from providers import ProviderError, get_provider
import inbox as inbox_module

bp = Blueprint("email_webhook", __name__)

UPLOAD_DIR = Path(os.environ.get("ADMINBOOKER_DATA_DIR") or Path(__file__).parent) / "uploads"
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)


def _verify_signature(signing_key: str, token: str, timestamp: str, signature: str) -> bool:
    """Controleer of het verzoek echt van Mailgun afkomt (HMAC-SHA256)."""
    verwacht = hmac.new(
        signing_key.encode("utf-8"),
        (timestamp + token).encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    return hmac.compare_digest(verwacht, signature)


def _to_book_payload(parsed: dict) -> dict:
    return {
        "leverancier": parsed.get("leverancier", {}),
        "factuurnummer": parsed.get("factuurnummer", ""),
        "datum": parsed.get("datum", ""),
        "vervaldatum": parsed.get("vervaldatum"),
        "valuta": parsed.get("valuta", "EUR"),
        "prijzen_incl_btw": parsed.get("prijzen_incl_btw", False),
        "regels": parsed.get("regels", []),
    }


@bp.route("/webhook/email", methods=["POST"])
def inbound_email():
    # --- 1. Handtekening verifiëren ---
    signing_key = os.environ.get("MAILGUN_SIGNING_KEY", "")
    if signing_key:
        token = request.form.get("token", "")
        timestamp = request.form.get("timestamp", "")
        signature = request.form.get("signature", "")
        if not _verify_signature(signing_key, token, timestamp, signature):
            return jsonify({"ok": False, "error": "Ongeldige handtekening"}), 403

    afzender = request.form.get("from", "onbekend")
    onderwerp = request.form.get("subject", "")

    # --- 2. PDF-bijlagen verzamelen ---
    pdf_bestanden = []
    i = 1
    while True:
        key = f"attachment-{i}"
        if key not in request.files:
            break
        f = request.files[key]
        if f.filename and f.filename.lower().endswith(".pdf"):
            pdf_bestanden.append(f)
        i += 1

    if not pdf_bestanden:
        print(f"[email] Geen PDF-bijlagen van {afzender!r} ({onderwerp!r}), overgeslagen.")
        # Mailgun verwacht 200 anders probeert hij het opnieuw
        return jsonify({"ok": True, "message": "Geen PDF bijlagen gevonden"}), 200

    # --- 3. Elke PDF door de pipeline sturen ---
    boeking_type = os.environ.get("EMAIL_BOEKING_TYPE", "inkoop").lower()
    resultaten = []

    for f in pdf_bestanden:
        safe_name = Path(f.filename).name
        target = UPLOAD_DIR / safe_name
        f.save(str(target))

        try:
            parsed = parse_pdf(target)

            if not parsed.get("factuurnummer") or not parsed.get("datum"):
                resultaten.append({
                    "bestand": safe_name,
                    "ok": False,
                    "error": "Kon factuurnummer of datum niet uit PDF halen",
                })
                continue

            payload = _to_book_payload(parsed)
            if boeking_type == "verkoop":
                factuur = boek_verkoop(payload, str(target), mark_open=True)
                doc_type = "sales_invoice"
            else:
                factuur = boek(payload, str(target))
                doc_type = "purchase_invoice"

            provider = get_provider()
            doc_url = provider.document_url(factuur["id"], doc_type)
            boeking_trace = factuur.get("_boeking_trace") or {}

            inbox_module.add({
                "doc_id": factuur.get("id"),
                "bestand": safe_name,
                "bron": "email",
                "afzender": afzender,
                "onderwerp": onderwerp,
                "leverancier": (boeking_trace.get("contact") or {}).get("naam"),
                "factuurnummer": factuur.get("reference") or factuur.get("invoice_number"),
                "datum": factuur.get("date") or factuur.get("invoice_date"),
                "totaal_incl_btw": boeking_trace.get("totaal_incl_btw"),
                "doc_url": doc_url,
                "provider": provider.name,
                "provider_display": provider.display_name,
                "regels": boeking_trace.get("regels", []),
                "min_zekerheid": boeking_trace.get("min_zekerheid"),
                "needs_review": bool(boeking_trace.get("needs_review")),
                "status": "te_controleren" if boeking_trace.get("needs_review") else "akkoord",
            })

            print(f"[email] Geboekt: {safe_name} → {doc_url}")
            resultaten.append({
                "bestand": safe_name,
                "ok": True,
                "factuurnummer": factuur.get("reference") or factuur.get("invoice_number"),
                "doc_url": doc_url,
            })

        except ProviderError as e:
            print(f"[email] Provider-fout bij {safe_name}: {e}")
            resultaten.append({"bestand": safe_name, "ok": False, "error": str(e)})
        except Exception as e:
            traceback.print_exc()
            resultaten.append({"bestand": safe_name, "ok": False, "error": str(e)})

    return jsonify({"ok": True, "resultaten": resultaten}), 200
