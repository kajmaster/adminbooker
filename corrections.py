"""
corrections.py - persistent 'correctie-geheugen' voor grootboek-classificatie.

Telkens als een boeking handmatig wordt gecorrigeerd (in de sandbox of straks
door de eigenaar via de mail-escalatie), onthouden we: deze regel-omschrijving
hoort bij dit grootboek. Bij een volgende (vrijwel) identieke regel kiest de
classifier dat grootboek dan deterministisch, met hoge zekerheid.

Dit is in de praktijk wat 'het model trainen' oplevert, maar zonder training:
het werkt per administratie, is meteen 100% op herhaalde regels, en je hoeft
nooit gevoelige facturen centraal te verzamelen.

Opslag: data/corrections.json
  { "<genormaliseerde omschrijving>": {
        "account_id": int, "account_name": str,
        "leverancier": str, "omschrijving": str,
        "count": int, "updated": "ISO" } , ... }
"""
from __future__ import annotations

import json
import os
import re
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
DATA = Path(os.environ.get("ADMINBOOKER_DATA_DIR") or HERE) / "data"
DATA.mkdir(parents=True, exist_ok=True)
PATH = DATA / "corrections.json"


def _norm(s) -> str:
    return re.sub(r"\s+", " ", (s or "").lower()).strip()


def _key(omschrijving, leverancier="") -> str:
    """Sleutel op genormaliseerde omschrijving. Leverancier bewaren we wel,
    maar we matchen primair op de omschrijving (generaliseert over leveranciers
    heen: 'webhosting' is bij elke leverancier hetzelfde grootboek)."""
    return _norm(omschrijving)


def load() -> dict:
    if PATH.exists():
        try:
            return json.loads(PATH.read_text(encoding="utf-8"))
        except (ValueError, OSError):
            return {}
    return {}


def lookup(store, omschrijving, leverancier=""):
    if not store:
        return None
    return store.get(_key(omschrijving, leverancier))


def add(omschrijving, leverancier, account_id, account_name) -> dict:
    """Voeg een correctie toe (of werk een bestaande bij) en schrijf weg."""
    store = load()
    k = _key(omschrijving, leverancier)
    entry = store.get(k) or {"count": 0}
    entry.update({
        "account_id": account_id,
        "account_name": account_name,
        "leverancier": leverancier or "",
        "omschrijving": omschrijving or "",
        "count": int(entry.get("count", 0)) + 1,
        "updated": time.strftime("%Y-%m-%dT%H:%M:%S"),
    })
    store[k] = entry
    tmp = PATH.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(store, indent=2, ensure_ascii=False), encoding="utf-8")
    tmp.replace(PATH)
    return entry


def all_entries() -> list:
    store = load()
    return sorted(store.values(), key=lambda e: e.get("updated", ""), reverse=True)
