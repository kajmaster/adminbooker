"""
inbox.py - 'Postvak / Te controleren' voor geboekte inkoopfacturen.

Elke factuur die via de normale flow wordt geboekt, krijgt hier een review-
record. De onzekere (needs_review) komen in het scherm bovenaan; de admin-kracht
kijkt ze na, corrigeert zo nodig de grootboekrekening (model leert) en zet ze op
akkoord. Dit is het dagelijkse werkscherm van de klant (los van de sandbox, die
puur voor batch-testen is).

Opslag: data/inbox.json  (lijst, nieuwste eerst, afgekapt op MAX records).
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
DATA = Path(os.environ.get("ADMINBOOKER_DATA_DIR") or HERE) / "data"
DATA.mkdir(parents=True, exist_ok=True)
PATH = DATA / "inbox.json"

MAX = 500  # bewaar de laatste N boekingen


def _load() -> list:
    if PATH.exists():
        try:
            data = json.loads(PATH.read_text(encoding="utf-8"))
            return data if isinstance(data, list) else []
        except (ValueError, OSError):
            return []
    return []


def _save(items: list):
    tmp = PATH.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(items[:MAX], indent=2, ensure_ascii=False),
                   encoding="utf-8")
    tmp.replace(PATH)


def add(record: dict) -> dict:
    """Voeg een geboekte factuur toe aan het postvak."""
    items = _load()
    rec = dict(record or {})
    rec.setdefault("id", str(int(time.time() * 1000)))
    rec["aangemaakt"] = time.strftime("%Y-%m-%dT%H:%M:%S")
    rec.setdefault("status", "te_controleren")
    items.insert(0, rec)
    _save(items)
    return rec


def list_items() -> list:
    return _load()


def _norm(s) -> str:
    return (s or "").strip().lower()


def find_duplicate(factuurnummer: str, leverancier: str = "",
                   datum: str = "", totaal: float | None = None) -> dict | None:
    """Geef de eerder geboekte factuur terug als deze een duplicaat lijkt, anders None.

    Twee herkenningen:
      1. zelfde factuurnummer + (zelfde of onbekende) leverancier  -> sterk signaal
      2. fallback als factuurnummer ontbreekt/onbetrouwbaar: zelfde leverancier
         + zelfde datum + (vrijwel) zelfde totaalbedrag

    Zo voorkom je dubbel boeken bij e-mail-retries of opnieuw uploaden.
    """
    fn, lv, dt = _norm(factuurnummer), _norm(leverancier), _norm(datum)
    for rec in _load():
        rec_fn = _norm(rec.get("factuurnummer"))
        rec_lv = _norm(rec.get("leverancier"))
        # 1. factuurnummer-match (leverancier mag matchen of onbekend zijn)
        if fn and rec_fn == fn and (not lv or not rec_lv or rec_lv == lv):
            return rec
        # 2. fallback: zelfde leverancier + datum + bedrag
        if totaal is not None and lv and rec_lv == lv and dt and _norm(rec.get("datum")) == dt:
            try:
                if abs(float(rec.get("totaal_incl_btw")) - float(totaal)) < 0.02:
                    return rec
            except (TypeError, ValueError):
                pass
    return None


def is_duplicate(factuurnummer: str, leverancier: str = "") -> bool:
    """Backwards-compatibele wrapper rond find_duplicate (alleen factuurnummer)."""
    if not factuurnummer:
        return False
    return find_duplicate(factuurnummer, leverancier) is not None


def update(item_id: str, **velden) -> dict | None:
    items = _load()
    for rec in items:
        if str(rec.get("id")) == str(item_id):
            rec.update(velden)
            _save(items)
            return rec
    return None
