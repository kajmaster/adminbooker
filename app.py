"""
app.py - AdminBooker webserver.

Start:
    python app.py

Open:
    http://localhost:5000

Endpoints:
    GET  /                - HTML UI (drag & drop)
    POST /api/book        - upload PDF, parse + boek in Moneybird
    GET  /api/health      - check API + administratie
"""
from __future__ import annotations

import os
import tempfile
import traceback
from pathlib import Path

from flask import Flask, abort, jsonify, redirect, render_template, request, send_from_directory, session, url_for

from providers import get_provider, ProviderError, available_providers, set_active_provider
from boek_agent import boek, boek_verkoop
from pdf_parser import parse_pdf
from bank_agent import import_en_match
import sandbox
import corrections
import notify
import inbox
from email_webhook import bp as email_bp

HERE = Path(__file__).resolve().parent
UPLOAD_DIR = Path(os.environ.get("ADMINBOOKER_DATA_DIR") or HERE) / "uploads"
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)

app = Flask(__name__, template_folder=str(HERE / "templates"))
app.config["MAX_CONTENT_LENGTH"] = 25 * 1024 * 1024  # 25MB max per PDF
app.secret_key = os.environ.get("SECRET_KEY") or os.urandom(32)
app.config["SESSION_COOKIE_HTTPONLY"] = True
app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
app.register_blueprint(email_bp)


# ---------- Login ----------
# Login is bewust uitgeschakeld voor de pilot: de app is direct bruikbaar
# zonder gebruikersnaam/wachtwoord. Bescherm de URL desgewenst op een andere
# manier (geheime link / netwerkbeperking) als dat later nodig is.


def _to_book_payload(parsed: dict) -> dict:
    """Map parser-output naar het format dat boek_agent.boek() verwacht."""
    return {
        "leverancier": parsed.get("leverancier", {}),
        "factuurnummer": parsed.get("factuurnummer", ""),
        "datum": parsed.get("datum", ""),
        "vervaldatum": parsed.get("vervaldatum"),
        "valuta": parsed.get("valuta", "EUR"),
        "prijzen_incl_btw": parsed.get("prijzen_incl_btw", False),
        "regels": parsed.get("regels", []),
    }


def _moneybird_url(admin_id: str, doc_id: str) -> str:
    return f"https://moneybird.com/{admin_id}/documents/{doc_id}"


# ---------- routes ----------

@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/health")
def health():
    try:
        provider = get_provider()
        info = provider.health_check()
        info["providers"] = available_providers()
        return jsonify(info)
    except Exception as e:
        return jsonify({
            "ok": False,
            "error": str(e),
            "providers": available_providers(),
        }), 500


@app.route("/api/providers", methods=["GET", "POST"])
def api_providers():
    if request.method == "POST":
        data = request.get_json(silent=True) or {}
        name = (data.get("name") or "").strip().lower()
        try:
            set_active_provider(name)
            # Test direct of de nieuwe provider werkt
            try:
                provider = get_provider()
                health = provider.health_check()
                return jsonify({"ok": True, "active": name, "health": health})
            except Exception as e:
                return jsonify({
                    "ok": True,
                    "active": name,
                    "health": {"ok": False, "error": str(e)},
                })
        except Exception as e:
            return jsonify({"ok": False, "error": str(e)}), 400
    return jsonify({"providers": available_providers()})


@app.route("/api/book", methods=["POST"])
def api_book():
    if "file" not in request.files:
        return jsonify({"ok": False, "error": "Geen bestand ontvangen"}), 400

    f = request.files["file"]
    if not f.filename:
        return jsonify({"ok": False, "error": "Geen bestandsnaam"}), 400

    # Expliciete provider-keuze meegestuurd vanuit de UI? Dan die activeren.
    req_provider = (request.form.get("provider") or "").strip().lower()
    if req_provider:
        try:
            set_active_provider(req_provider)
        except Exception:
            pass  # onbekende naam -> val terug op huidige actieve

    # 'inkoop' (default), 'verkoop' of 'bank'
    boeking_type = (request.form.get("type") or "inkoop").strip().lower()
    if boeking_type not in ("inkoop", "verkoop", "bank"):
        return jsonify({"ok": False, "error": f"Onbekend type '{boeking_type}'"}), 400

    # Bank: aparte flow (CSV/MT940 ipv PDF)
    if boeking_type == "bank":
        return _handle_bank_upload(f)

    if not f.filename.lower().endswith(".pdf"):
        return jsonify({"ok": False, "error": "Alleen PDF toegestaan"}), 400

    # tijdelijk opslaan
    safe_name = Path(f.filename).name
    target = UPLOAD_DIR / safe_name
    f.save(str(target))

    try:
        # 1. parse PDF
        parsed = parse_pdf(target)
        if not parsed.get("factuurnummer") or not parsed.get("datum"):
            return jsonify({
                "ok": False,
                "stage": "parse",
                "error": "Kon factuurnummer of datum niet uit PDF halen",
                "parsed": {k: v for k, v in parsed.items() if not k.startswith("_raw")},
            }), 422

        # 2. boek in Moneybird (inkoop of verkoop)
        payload = _to_book_payload(parsed)
        if boeking_type == "verkoop":
            factuur = boek_verkoop(payload, str(target), mark_open=True)
            mb_path_segment = "sales_invoices"
        else:
            factuur = boek(payload, str(target))
            mb_path_segment = "documents"

        provider = get_provider()
        # URL naar de factuur in de UI van het pakket
        doc_type = "sales_invoice" if boeking_type == "verkoop" else "purchase_invoice"
        mb_url = provider.document_url(factuur["id"], doc_type)

        # Twijfel-escalatie: bij lage zekerheid de eigenaar mailen. In de sandbox
        # zetten we dit uit (notify=false) om geen 50 mails te sturen tijdens testen.
        boeking_trace = factuur.get("_boeking_trace") or {}
        notify_aan = (request.form.get("notify") or "true").strip().lower() != "false"
        notify_resultaat = None
        if notify_aan and boeking_trace.get("needs_review"):
            try:
                onzeker = [
                    r for r in boeking_trace.get("regels", [])
                    if (r.get("zekerheid") or 0) < 0.5
                ]
                notify_resultaat = notify.notify_low_confidence(
                    leverancier=(boeking_trace.get("contact") or {}).get("naam"),
                    factuurnummer=factuur.get("reference") or factuur.get("invoice_number"),
                    doc_url=mb_url,
                    onzekere_regels=onzeker,
                )
            except Exception as e:  # mag de boeking nooit laten falen
                notify_resultaat = {"sent": False, "reason": str(e)}

        # Voeg de boeking toe aan het postvak 'Te controleren' (alleen inkoop).
        # De sandbox stuurt inbox=false zodat testboekingen het postvak niet vervuilen.
        inbox_aan = (request.form.get("inbox") or "true").strip().lower() != "false"
        if inbox_aan and boeking_type != "verkoop":
            try:
                inbox.add({
                    "doc_id": factuur.get("id"),
                    "bestand": safe_name,
                    "leverancier": (boeking_trace.get("contact") or {}).get("naam"),
                    "factuurnummer": factuur.get("reference") or factuur.get("invoice_number"),
                    "datum": factuur.get("date") or factuur.get("invoice_date"),
                    "totaal_incl_btw": boeking_trace.get("totaal_incl_btw"),
                    "doc_url": mb_url,
                    "provider": provider.name,
                    "provider_display": provider.display_name,
                    "regels": boeking_trace.get("regels", []),
                    "min_zekerheid": boeking_trace.get("min_zekerheid"),
                    "needs_review": bool(boeking_trace.get("needs_review")),
                    "status": "te_controleren" if boeking_trace.get("needs_review") else "akkoord",
                })
            except Exception:
                traceback.print_exc()

        return jsonify({
            "ok": True,
            "type": boeking_type,
            "provider": provider.name,
            "provider_display": provider.display_name,
            "moneybird_id": factuur["id"],
            "moneybird_url": mb_url,
            "contact_naam": (
                factuur.get("contact", {}).get("company_name")
                or factuur.get("cached_contact", {}).get("name")
                or factuur.get("cached_contact", {}).get("contact_person_name")
            ),
            "leverancier": (
                factuur.get("contact", {}).get("company_name")
                or factuur.get("cached_contact", {}).get("name")
            ),  # legacy
            "factuurnummer": (
                factuur.get("reference")
                or factuur.get("invoice_id")
                or factuur.get("invoice_number")
            ),
            "datum": factuur.get("date") or factuur.get("invoice_date"),
            "totaal_incl_btw": factuur.get("total_price_incl_tax"),
            "valuta": factuur.get("currency"),
            "state": factuur.get("state"),
            # Beslissings-trace (grootboek/BTW/contact per regel) voor de sandbox.
            # De gewone single-upload UI negeert dit veld.
            "boeking": factuur.get("_boeking_trace"),
            "needs_review": boeking_trace.get("needs_review"),
            "notify": notify_resultaat,
            "parsed": {k: v for k, v in parsed.items() if not k.startswith("_raw")},
        })
    except ProviderError as e:
        return jsonify({
            "ok": False,
            "stage": "provider",
            "error": str(e),
        }), 502
    except Exception as e:
        traceback.print_exc()
        return jsonify({
            "ok": False,
            "stage": "internal",
            "error": str(e),
        }), 500


def _handle_bank_upload(f):
    """Bank bestand: parse + import + match."""
    name_lower = f.filename.lower()
    allowed = (".csv", ".tsv", ".txt", ".mt940", ".sta", ".940")
    if not name_lower.endswith(allowed):
        return jsonify({
            "ok": False,
            "error": "Voor bank: CSV, TSV, TXT of MT940 toegestaan",
        }), 400

    safe_name = Path(f.filename).name
    target = UPLOAD_DIR / safe_name
    f.save(str(target))

    try:
        result = import_en_match(str(target))
        return jsonify({
            "ok": True,
            "type": "bank",
            "statement_id": result["statement_id"],
            "totaal_mutaties": result["totaal_mutaties"],
            "gekoppeld": result["gekoppeld"],
            "geen_match": result["geen_match"],
            "details": result["details"],
        })
    except ProviderError as e:
        return jsonify({"ok": False, "stage": "provider", "error": str(e)}), 502
    except Exception as e:
        traceback.print_exc()
        return jsonify({"ok": False, "stage": "internal", "error": str(e)}), 500


# ---------- sandbox (batch boeken + accuracy review) ----------

@app.route("/sandbox")
def sandbox_page():
    return render_template("sandbox.html")


@app.route("/api/sandbox/save", methods=["POST"])
def api_sandbox_save():
    data = request.get_json(silent=True) or {}
    run_id = (data.get("run_id") or "").strip()
    if not run_id:
        return jsonify({"ok": False, "error": "run_id ontbreekt"}), 400
    try:
        rid = sandbox.save_run(run_id, data)
        return jsonify({"ok": True, "run_id": rid})
    except Exception as e:
        traceback.print_exc()
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route("/api/sandbox/load/<run_id>")
def api_sandbox_load(run_id):
    data = sandbox.load_run(run_id)
    if data is None:
        return jsonify({"ok": False, "error": "Run niet gevonden"}), 404
    return jsonify({"ok": True, "run": data})


@app.route("/api/sandbox/runs")
def api_sandbox_runs():
    return jsonify({"ok": True, "runs": sandbox.list_runs()})


@app.route("/api/sandbox/pdf/<path:name>")
def api_sandbox_pdf(name):
    """Serveer een eerder geuploade PDF terug (voor de review-preview)."""
    safe = Path(name).name
    if not (UPLOAD_DIR / safe).exists():
        abort(404)
    return send_from_directory(str(UPLOAD_DIR), safe)


@app.route("/api/ledgers")
def api_ledgers():
    """Lijst van kosten-grootboeken voor de 'leer de juiste rekening'-dropdown."""
    req_provider = (request.args.get("provider") or "").strip().lower()
    if req_provider:
        try:
            set_active_provider(req_provider)
        except Exception:
            pass
    try:
        provider = get_provider()
        import html as _html
        ledgers = provider.purchase_ledgers()
        out = [{
            "id": l.get("id"),
            "name": _html.unescape(l.get("name") or ""),
            "path": _html.unescape(l.get("path_name") or l.get("name") or ""),
        } for l in ledgers]
        return jsonify({"ok": True, "ledgers": out})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route("/api/sandbox/correct", methods=["POST"])
def api_sandbox_correct():
    """Sla een handmatige grootboek-correctie op in het correctie-geheugen.

    Body: {omschrijving, leverancier, account_id, account_name}
    """
    data = request.get_json(silent=True) or {}
    omschrijving = (data.get("omschrijving") or "").strip()
    if not omschrijving:
        return jsonify({"ok": False, "error": "omschrijving ontbreekt"}), 400
    try:
        entry = corrections.add(
            omschrijving,
            data.get("leverancier") or "",
            data.get("account_id"),
            data.get("account_name") or "",
        )
        return jsonify({"ok": True, "entry": entry})
    except Exception as e:
        traceback.print_exc()
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route("/api/sandbox/cleanup", methods=["POST"])
def api_sandbox_cleanup():
    """Verwijder de geboekte inkoopfacturen (expenses) van een run weer.

    Body: {"ids": [<document_id>, ...], "provider": "rompslomp"}
    Best-effort: rapporteert per id of het lukte.
    """
    data = request.get_json(silent=True) or {}
    ids = data.get("ids") or []
    req_provider = (data.get("provider") or "").strip().lower()
    if req_provider:
        try:
            set_active_provider(req_provider)
        except Exception:
            pass
    try:
        provider = get_provider()
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500

    deleter = getattr(provider, "delete_purchase_invoice", None)
    if not callable(deleter):
        return jsonify({
            "ok": False,
            "error": f"{provider.display_name} ondersteunt geen verwijderen via API.",
        }), 400

    verwijderd, mislukt = [], []
    for doc_id in ids:
        try:
            deleter(doc_id)
            verwijderd.append(doc_id)
        except Exception as e:
            mislukt.append({"id": doc_id, "error": str(e)})
    return jsonify({
        "ok": True,
        "verwijderd": verwijderd,
        "mislukt": mislukt,
        "aantal_verwijderd": len(verwijderd),
    })


# ---------- postvak 'Te controleren' ----------

@app.route("/inbox")
def inbox_page():
    return render_template("inbox.html")


@app.route("/api/inbox")
def api_inbox():
    items = inbox.list_items()
    te_controleren = sum(1 for i in items if i.get("status") == "te_controleren")
    return jsonify({"ok": True, "items": items, "te_controleren": te_controleren})


@app.route("/api/inbox/akkoord", methods=["POST"])
def api_inbox_akkoord():
    data = request.get_json(silent=True) or {}
    item_id = data.get("id")
    status = (data.get("status") or "akkoord")
    rec = inbox.update(item_id, status=status)
    if rec is None:
        return jsonify({"ok": False, "error": "Niet gevonden"}), 404
    return jsonify({"ok": True, "item": rec})


# ---------- email poller trigger ----------

@app.route("/api/email/poll", methods=["POST"])
def api_email_poll():
    """Handmatig of via cron triggeren: verwerk ongelezen emails met PDF-bijlagen."""
    try:
        from email_poller import poll_once
        resultaat = poll_once()
        return jsonify({"ok": True, **resultaat})
    except ValueError as e:
        return jsonify({"ok": False, "error": str(e)}), 400
    except Exception as e:
        traceback.print_exc()
        return jsonify({"ok": False, "error": str(e)}), 500


if __name__ == "__main__":
    print()
    print("=" * 60)
    print(" AdminBooker is gestart")
    print("=" * 60)
    print(" Open in je browser:  http://localhost:5000")
    print(" Stop met:            Ctrl+C")
    print("=" * 60)
    print()
    # Geen debug mode in productie. Voor lokale demo OK.
    app.run(host="127.0.0.1", port=5000, debug=False)
