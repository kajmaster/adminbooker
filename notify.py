"""
notify.py - stuur de eigenaar een mailtje als AdminBooker over een boeking
twijfelt (lage zekerheid). Config via .env; doet niets (no-op) zolang er geen
SMTP is ingesteld, zodat de rest van de app gewoon blijft werken.

.env-sleutels:
  SMTP_HOST=smtp.gmail.com
  SMTP_PORT=587
  SMTP_USER=jij@gmail.com
  SMTP_PASS=<app-wachtwoord>        # Gmail: maak een 'app-wachtwoord' aan
  ALERT_EMAIL_FROM=jij@gmail.com    # optioneel, default = SMTP_USER
  ALERT_EMAIL_TO=eigenaar@bedrijf.nl

Later (stap 3+) kan hetzelfde mechanisme een WhatsApp/Telegram-bericht sturen;
de aanroep blijft gelijk.
"""
from __future__ import annotations

import os
import smtplib
from email.message import EmailMessage


def _cfg():
    return {
        "host": os.environ.get("SMTP_HOST"),
        "port": int(os.environ.get("SMTP_PORT") or 587),
        "user": os.environ.get("SMTP_USER"),
        "pass": os.environ.get("SMTP_PASS"),
        "from": os.environ.get("ALERT_EMAIL_FROM") or os.environ.get("SMTP_USER"),
        "to": os.environ.get("ALERT_EMAIL_TO"),
    }


def is_configured() -> bool:
    c = _cfg()
    return bool(c["host"] and c["user"] and c["pass"] and c["to"])


def send_email(subject: str, body: str) -> dict:
    """Stuur een platte-tekst mail. Retourneer {sent, reason}."""
    c = _cfg()
    if not is_configured():
        return {"sent": False, "reason": "SMTP niet geconfigureerd in .env"}
    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = c["from"]
    msg["To"] = c["to"]
    msg.set_content(body)
    try:
        with smtplib.SMTP(c["host"], c["port"], timeout=20) as s:
            s.starttls()
            s.login(c["user"], c["pass"])
            s.send_message(msg)
        return {"sent": True}
    except Exception as e:  # noqa: BLE001 - mag de boeking nooit laten falen
        return {"sent": False, "reason": str(e)}


def review_email_tekst(leverancier, factuurnummer, doc_url, onzekere_regels) -> str:
    """Bouw de mailtekst voor een twijfel-boeking."""
    regels = []
    for r in onzekere_regels:
        zeker = int(round((r.get("zekerheid") or 0) * 100))
        alts = ", ".join(r.get("alternatieven") or []) or "-"
        regels.append(
            f"- {r.get('omschrijving')}\n"
            f"    gekozen: {r.get('grootboek_naam')} (zekerheid {zeker}%)\n"
            f"    mogelijke alternatieven: {alts}"
        )
    return (
        "AdminBooker heeft een factuur geboekt maar twijfelt over de "
        "grootboekindeling. Kijk dit even na:\n\n"
        f"Leverancier: {leverancier or '-'}\n"
        f"Factuur: {factuurnummer or '-'}\n\n"
        "Onzekere regels:\n" + "\n".join(regels) + "\n\n"
        + (f"Bekijk/aanpassen: {doc_url}\n" if doc_url else "")
        + "\nZodra je de juiste rekening kiest, onthoudt AdminBooker dat voor "
        "volgende keren."
    )


def notify_low_confidence(leverancier, factuurnummer, doc_url, onzekere_regels) -> dict:
    if not onzekere_regels:
        return {"sent": False, "reason": "geen onzekere regels"}
    subject = f"AdminBooker: controleer boeking {factuurnummer or ''}".strip()
    body = review_email_tekst(leverancier, factuurnummer, doc_url, onzekere_regels)
    return send_email(subject, body)
