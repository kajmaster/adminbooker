"""
email_poller.py - Haal inkoopfacturen op uit een emailpostvak (IMAP).

Bouwbedrijven sturen facturen door naar een vast e-mailadres.
Deze poller kijkt periodiek in dat postvak, haalt PDF-bijlagen op en
boekt ze automatisch in Rompslomp/Moneybird via de bestaande pipeline.

Geen externe diensten nodig — werkt met elk IMAP-postvak:
Gmail, Outlook, TransIP, Hostnet, noem maar op.

Gebruik:
    python email_poller.py              # eenmalig uitvoeren
    python email_poller.py --loop 300   # elke 5 minuten herhalen

Of trigger via het Flask-endpoint:
    POST /api/email/poll

Omgevingsvariabelen (.env):
    IMAP_HOST        bijv. imap.gmail.com of imap.outlook.com
    IMAP_PORT        993 (SSL, default)
    IMAP_USER        facturen@jouwbbedrijf.nl
    IMAP_PASSWORD    app-wachtwoord (zie README)
    IMAP_FOLDER      INBOX (default)
    EMAIL_BOEKING_TYPE  inkoop (default) of verkoop

Gmail: zet IMAP aan in Instellingen → Doorsturen en POP/IMAP,
       gebruik een App-wachtwoord (vereist 2-staps verificatie).
Outlook/Hotmail: imap.outlook.com, gewoon wachtwoord of App-wachtwoord.
"""
from __future__ import annotations

import email
import imaplib
import os
import sys
import time
import traceback
from email.header import decode_header
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent

# Laad .env als het script standalone draait
try:
    from dotenv import load_dotenv
    load_dotenv(BASE_DIR / ".env")
except ImportError:
    pass  # python-dotenv niet geïnstalleerd → variabelen moeten al gezet zijn

from boek_agent import boek, boek_verkoop
from pdf_parser import parse_pdf
from providers import ProviderError, get_provider
import inbox as inbox_module

UPLOAD_DIR = Path(os.environ.get("ADMINBOOKER_DATA_DIR") or BASE_DIR) / "uploads"
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)


# ---------- IMAP helpers ----------

def _imap_connect() -> imaplib.IMAP4_SSL:
    host = os.environ.get("IMAP_HOST", "").strip()
    port = int(os.environ.get("IMAP_PORT", "993"))
    user = os.environ.get("IMAP_USER", "").strip()
    password = os.environ.get("IMAP_PASSWORD", "").strip()

    if not host or not user or not password:
        raise ValueError(
            "Stel IMAP_HOST, IMAP_USER en IMAP_PASSWORD in je .env in."
        )

    conn = imaplib.IMAP4_SSL(host, port)
    conn.login(user, password)
    return conn


def _decode_filename(raw: str | bytes | None) -> str:
    if raw is None:
        return ""
    if isinstance(raw, bytes):
        return raw.decode("utf-8", errors="replace")
    parts = decode_header(raw)
    naam = ""
    for deel, enc in parts:
        if isinstance(deel, bytes):
            naam += deel.decode(enc or "utf-8", errors="replace")
        else:
            naam += deel
    return naam


def _pdf_parts(msg: email.message.Message) -> list[tuple[str, bytes]]:
    """Geef alle (bestandsnaam, bytes) paren terug voor PDF-bijlagen."""
    resultaat = []
    for part in msg.walk():
        ct = part.get_content_type()
        cd = part.get("Content-Disposition", "")
        naam = _decode_filename(
            part.get_filename()
            or part.get_param("name")
        )
        if (ct == "application/pdf" or naam.lower().endswith(".pdf")) and "attachment" in cd.lower():
            data = part.get_payload(decode=True)
            if data:
                resultaat.append((naam or "bijlage.pdf", data))
    return resultaat


# ---------- boeking helpers ----------

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


def _verwerk_pdf(naam: str, data: bytes, afzender: str, onderwerp: str, boeking_type: str) -> dict:
    """Sla PDF op, parse en boek. Geeft resultaat-dict terug."""
    safe_naam = Path(naam).name or "bijlage.pdf"
    target = UPLOAD_DIR / safe_naam
    target.write_bytes(data)

    try:
        parsed = parse_pdf(target)
        if not parsed.get("factuurnummer") or not parsed.get("datum"):
            return {"bestand": safe_naam, "ok": False, "error": "Kon factuurnummer of datum niet lezen"}

        # Dubbele boeking voorkomen
        leverancier_naam = (parsed.get("leverancier") or {}).get("naam", "")
        if inbox_module.is_duplicate(parsed["factuurnummer"], leverancier_naam):
            print(f"[email] ⚠ Overgeslagen (al ingeboekt): {parsed['factuurnummer']} – {leverancier_naam}")
            return {"bestand": safe_naam, "ok": True, "dubbel": True,
                    "factuurnummer": parsed["factuurnummer"]}

        payload = _to_book_payload(parsed)
        if boeking_type == "verkoop":
            factuur = boek_verkoop(payload, str(target), mark_open=True)
            doc_type = "sales_invoice"
        else:
            factuur = boek(payload, str(target))
            doc_type = "purchase_invoice"

        provider = get_provider()
        doc_url = provider.document_url(factuur["id"], doc_type)
        trace = factuur.get("_boeking_trace") or {}

        inbox_module.add({
            "doc_id": factuur.get("id"),
            "bestand": safe_naam,
            "bron": "email",
            "afzender": afzender,
            "onderwerp": onderwerp,
            "leverancier": (trace.get("contact") or {}).get("naam"),
            "factuurnummer": factuur.get("reference") or factuur.get("invoice_number"),
            "datum": factuur.get("date") or factuur.get("invoice_date"),
            "totaal_incl_btw": trace.get("totaal_incl_btw"),
            "doc_url": doc_url,
            "provider": provider.name,
            "provider_display": provider.display_name,
            "regels": trace.get("regels", []),
            "min_zekerheid": trace.get("min_zekerheid"),
            "needs_review": bool(trace.get("needs_review")),
            "status": "te_controleren" if trace.get("needs_review") else "akkoord",
        })

        print(f"[email] ✓ {safe_naam} → {doc_url}")
        return {
            "bestand": safe_naam,
            "ok": True,
            "factuurnummer": factuur.get("reference") or factuur.get("invoice_number"),
            "doc_url": doc_url,
        }

    except ProviderError as e:
        print(f"[email] Provider-fout bij {safe_naam}: {e}")
        return {"bestand": safe_naam, "ok": False, "error": str(e)}
    except Exception as e:
        traceback.print_exc()
        return {"bestand": safe_naam, "ok": False, "error": str(e)}


# ---------- hoofd-poll ----------

def poll_once() -> dict:
    """
    Verwerk alle ongelezen emails met PDF-bijlagen.
    Geeft {"verwerkt": int, "overgeslagen": int, "fouten": int, "details": list} terug.
    """
    boeking_type = os.environ.get("EMAIL_BOEKING_TYPE", "inkoop").lower()
    folder = os.environ.get("IMAP_FOLDER", "INBOX")

    conn = _imap_connect()
    try:
        conn.select(folder)
        _, data = conn.search(None, "UNSEEN")
        msg_ids = data[0].split() if data[0] else []

        print(f"[email] {len(msg_ids)} ongelezen bericht(en) in {folder}")

        details = []
        for msg_id in msg_ids:
            _, raw = conn.fetch(msg_id, "(RFC822)")
            if not raw or not raw[0]:
                continue

            msg = email.message_from_bytes(raw[0][1])
            afzender = msg.get("From", "onbekend")
            onderwerp = _decode_filename(msg.get("Subject", ""))
            pdf_delen = _pdf_parts(msg)

            if not pdf_delen:
                print(f"[email] Geen PDF in bericht van {afzender!r}, overgeslagen")
                conn.store(msg_id, "+FLAGS", "\\Seen")
                details.append({"afzender": afzender, "ok": None, "reden": "geen PDF"})
                continue

            for naam, data_bytes in pdf_delen:
                res = _verwerk_pdf(naam, data_bytes, afzender, onderwerp, boeking_type)
                details.append(res)

            # Als alles gelukt is → als gelezen markeren
            if all(d.get("ok") is not False for d in details[-len(pdf_delen):]):
                conn.store(msg_id, "+FLAGS", "\\Seen")

        verwerkt = sum(1 for d in details if d.get("ok") is True)
        overgeslagen = sum(1 for d in details if d.get("ok") is None)
        fouten = sum(1 for d in details if d.get("ok") is False)

        return {
            "verwerkt": verwerkt,
            "overgeslagen": overgeslagen,
            "fouten": fouten,
            "details": details,
        }
    finally:
        try:
            conn.logout()
        except Exception:
            pass


# ---------- standalone uitvoering ----------

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="AdminBooker email poller")
    parser.add_argument("--loop", type=int, default=0,
                        metavar="SECONDEN",
                        help="Herhaal elke N seconden (0 = eenmalig)")
    args = parser.parse_args()

    if args.loop > 0:
        print(f"[email] Poller gestart, interval: {args.loop}s. Stop met Ctrl+C.")
        while True:
            try:
                resultaat = poll_once()
                print(f"[email] verwerkt={resultaat['verwerkt']} "
                      f"overgeslagen={resultaat['overgeslagen']} "
                      f"fouten={resultaat['fouten']}")
            except Exception as e:
                print(f"[email] Fout: {e}")
            time.sleep(args.loop)
    else:
        try:
            resultaat = poll_once()
            print(resultaat)
            sys.exit(0 if resultaat["fouten"] == 0 else 1)
        except Exception as e:
            print(f"Fout: {e}")
            sys.exit(1)
