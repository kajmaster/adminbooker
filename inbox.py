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


def is_duplicate(factuurnummer: str, leverancier: str = "") -> bool:
    """Controleer of dit factuurnummer al eerder is ingeboekt.

    Vergelijkt op factuurnummer + leverancier (genormaliseerd).
    Zo voorkom je dubbele boekingen bij email-retries of handmatig opnieuw uploaden.
    """
    if not factuurnummer:
        return False
    fn = factuurnummer.strip().lower()
    lv = (leverancier or "").strip().lower()
    for rec in _load():
        rec_fn = (rec.get("factuurnummer") or "").strip().lower()
        rec_lv = (rec.get("leverancier") or "").strip().lower()
        if rec_fn == fn and (not lv or not rec_lv or rec_lv == lv):
            return True
    return False


def update(item_id: str, **velden) -> dict | None:
    items = _load()
    for rec in items:
        if str(rec.get("id")) == str(item_id):
            rec.update(velden)
            _save(items)
            return rec
    return None
