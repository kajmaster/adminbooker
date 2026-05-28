"""
sandbox.py - opslag voor sandbox-/accuracy-runs van AdminBoeker.

Een 'run' is een batch facturen die door de echte boekflow is gehaald, plus de
handmatige review-uitkomsten (per veld goed/fout). We bewaren elke run als JSON
in tests/sandbox_runs/<run_id>.json zodat het nalopen een herlaad/refresh
overleeft en je achteraf het accuracy-rapport kunt teruglezen.

De backend bewaart de run-state vrijwel ongeparsed: de sandbox-frontend bepaalt
de structuur (results + reviews). Hier doen we alleen veilige opslag, laden en
een lichte samenvatting voor de runs-lijst.
"""
from __future__ import annotations

import json
import os
import re
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
if os.environ.get("ADMINBOOKER_DATA_DIR"):
    RUNS_DIR = Path(os.environ["ADMINBOOKER_DATA_DIR"]) / "sandbox_runs"
else:
    RUNS_DIR = HERE / "tests" / "sandbox_runs"
RUNS_DIR.mkdir(parents=True, exist_ok=True)

_SAFE = re.compile(r"[^A-Za-z0-9_\-]")


def _safe_run_id(run_id: str) -> str:
    """Maak een run_id veilig als bestandsnaam (geen path traversal)."""
    rid = _SAFE.sub("_", (run_id or "").strip())
    return rid[:120] or f"run_{int(time.time())}"


def _path(run_id: str) -> Path:
    return RUNS_DIR / f"{_safe_run_id(run_id)}.json"


def save_run(run_id: str, payload: dict) -> str:
    """Schrijf de volledige run-state weg. Retourneer het (veilige) run_id."""
    rid = _safe_run_id(run_id)
    data = dict(payload or {})
    data["run_id"] = rid
    data.setdefault("created", time.strftime("%Y-%m-%dT%H:%M:%S"))
    data["updated"] = time.strftime("%Y-%m-%dT%H:%M:%S")
    tmp = _path(rid).with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    tmp.replace(_path(rid))
    return rid


def load_run(run_id: str):
    """Laad een run-state, of None als die niet bestaat."""
    p = _path(run_id)
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (ValueError, OSError):
        return None


def list_runs():
    """Lijst van runs (nieuwste eerst) met een lichte samenvatting."""
    runs = []
    for p in RUNS_DIR.glob("*.json"):
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
        except (ValueError, OSError):
            continue
        results = data.get("results") or []
        runs.append({
            "run_id": data.get("run_id") or p.stem,
            "created": data.get("created"),
            "updated": data.get("updated"),
            "naam": data.get("naam") or "",
            "aantal": len(results),
            "provider": data.get("provider"),
        })
    runs.sort(key=lambda r: (r.get("updated") or r.get("created") or ""), reverse=True)
    return runs


def delete_run(run_id: str) -> bool:
    p = _path(run_id)
    if p.exists():
        p.unlink()
        return True
    return False
