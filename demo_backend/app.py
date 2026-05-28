"""
demo_backend/app.py — publieke demo-backend voor adminbooker.com.

In tegenstelling tot de hoofd-app:
    - GEEN Moneybird-koppeling
    - GEEN authenticatie
    - alleen extractie via pdf_parser.parse_pdf()
    - rate-limit per IP (in-memory, simpel)
    - PDF wordt direct na verwerking gewist (geen opslag)
    - CORS open voor adminbooker.com + localhost (dev)

Endpoints:
    GET  /api/health         — health probe (gebruikt door frontend)
    POST /api/demo-extract   — multipart/form-data, veld `file` (PDF)
                               retourneert {ok: true, data: {...}} met de
                               geparseerde factuurdata. Geen _confidence /
                               _raw_text velden in de response (privacy).

Lokaal draaien:
    pip install -r requirements.txt
    python app.py
    -> http://localhost:8080/api/health
"""
from __future__ import annotations

import os
import tempfile
import time
from collections import defaultdict, deque
from pathlib import Path

from flask import Flask, jsonify, request
from flask_cors import CORS

from pdf_parser import parse_pdf

# ---------- config ----------
HERE = Path(__file__).resolve().parent
MAX_BYTES = 10 * 1024 * 1024  # 10 MB per PDF in de demo

# Welke origins mogen de demo aanroepen?
ALLOWED_ORIGINS = [
    "https://adminbooker.com",
    "https://www.adminbooker.com",
    # Netlify deploy-preview wildcard (matching wordt handmatig in CORS afgehandeld):
    "http://localhost:3000",
    "http://localhost:5000",
    "http://localhost:5173",
    "http://127.0.0.1:5500",
]

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = MAX_BYTES

CORS(
    app,
    resources={r"/api/*": {"origins": ALLOWED_ORIGINS}},
    methods=["GET", "POST", "OPTIONS"],
    allow_headers=["Content-Type"],
    max_age=3600,
)


# ---------- simpele rate-limit per IP ----------
# 20 requests per 10 minuten — voldoende voor een demo, niet voor bulk.
RATE_WINDOW_SEC = 600
RATE_MAX = 20
_RATE_HITS: dict[str, deque] = defaultdict(deque)


def _client_ip() -> str:
    # Render/Netlify stoppen het echte adres in X-Forwarded-For
    fwd = request.headers.get("X-Forwarded-For", "")
    if fwd:
        return fwd.split(",")[0].strip()
    return request.remote_addr or "unknown"


def _rate_limit_ok() -> bool:
    ip = _client_ip()
    now = time.time()
    hits = _RATE_HITS[ip]
    cutoff = now - RATE_WINDOW_SEC
    while hits and hits[0] < cutoff:
        hits.popleft()
    if len(hits) >= RATE_MAX:
        return False
    hits.append(now)
    return True


def _strip_internals(data: dict) -> dict:
    """Verwijder velden die we niet aan de klant willen tonen."""
    return {k: v for k, v in data.items() if not k.startswith("_")}


# ---------- routes ----------

@app.route("/api/health")
def health():
    return jsonify({
        "ok": True,
        "service": "adminbooker-demo",
        "version": "1.0.0",
    })


@app.route("/api/demo-extract", methods=["POST", "OPTIONS"])
def demo_extract():
    if request.method == "OPTIONS":
        return ("", 204)

    if not _rate_limit_ok():
        return jsonify({
            "ok": False,
            "error": "Even rustig aan — te veel verzoeken vanaf je IP. Probeer over een paar minuten opnieuw.",
        }), 429

    f = request.files.get("file")
    if not f or not f.filename:
        return jsonify({"ok": False, "error": "Geen bestand ontvangen."}), 400

    name = f.filename.lower()
    if not name.endswith(".pdf"):
        return jsonify({"ok": False, "error": "Alleen PDF-bestanden worden geaccepteerd."}), 400

    # Tijdelijk opslaan, parsen, daarna direct verwijderen
    tmp_dir = Path(tempfile.mkdtemp(prefix="adminbooker_demo_"))
    tmp_path = tmp_dir / "factuur.pdf"
    try:
        f.save(str(tmp_path))
        # check daadwerkelijke grootte
        if tmp_path.stat().st_size > MAX_BYTES:
            return jsonify({"ok": False, "error": "PDF groter dan 10 MB."}), 413

        parsed = parse_pdf(tmp_path)
        clean = _strip_internals(parsed)
        return jsonify({"ok": True, "data": clean})
    except Exception as e:
        # Geen stack-trace teruggeven, wel iets bruikbaars in logs
        app.logger.exception("demo-extract faalde")
        return jsonify({
            "ok": False,
            "error": f"Kon PDF niet verwerken: {type(e).__name__}",
        }), 500
    finally:
        # ALTIJD opruimen, ook bij errors
        try:
            if tmp_path.exists():
                tmp_path.unlink()
            tmp_dir.rmdir()
        except Exception:
            pass


@app.errorhandler(413)
def too_large(_e):
    return jsonify({"ok": False, "error": "Bestand te groot — max 10 MB."}), 413


# ---------- lokaal draaien ----------
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8080))
    app.run(host="0.0.0.0", port=port, debug=False)
