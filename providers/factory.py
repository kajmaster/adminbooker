"""
factory.py - kies de juiste provider op basis van .env / runtime keuze.

Gebruik:
    from providers import get_provider
    provider = get_provider()

Of expliciet:
    provider = get_provider("exact")
"""

from __future__ import annotations

import os
from pathlib import Path

from .base import AccountingProvider, ProviderError


# Lazy-import zodat we niet meteen credentials checken bij import
_PROVIDERS = {
    "moneybird": ("providers.moneybird_provider", "MoneybirdProvider"),
    "rompslomp": ("providers.rompslomp_provider", "RompslompProvider"),
    "exact": ("providers.exact_provider", "ExactOnlineProvider"),
}

# Tijdelijk verborgen providers: blijven werken in code, maar verschijnen niet
# in de keuzelijst van de UI. Haal een naam hier weg om 'm weer te tonen.
_HIDDEN_PROVIDERS = {"moneybird"}

# In-memory cache: 1 provider-instance per naam
_instances: dict[str, AccountingProvider] = {}

# Actieve provider naam (kan runtime gewijzigd via set_active_provider)
_active: str | None = None


def _load_env_once():
    """Laad .env eenmalig in os.environ als 'ie er is."""
    here = Path(__file__).resolve().parent.parent
    env_path = here / ".env"
    if not env_path.exists():
        return
    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip())


_load_env_once()


def available_providers() -> list[dict]:
    """Lijst alle providers met basis-info."""
    info = []
    for key, (modpath, cls) in _PROVIDERS.items():
        if key in _HIDDEN_PROVIDERS:
            continue
        display = {
            "moneybird": "Moneybird",
            "rompslomp": "Rompslomp (beta)",
            "exact": "Exact Online",
        }.get(key, key)
        # Indicatie of credentials aanwezig zijn
        configured = False
        if key == "moneybird":
            configured = bool(os.environ.get("MONEYBIRD_API_KEY")
                              and os.environ.get("MONEYBIRD_ADMINISTRATION_ID"))
        elif key == "rompslomp":
            # COMPANY_ID is optioneel - wordt automatisch ontdekt via /companies
            configured = bool(os.environ.get("ROMPSLOMP_API_TOKEN"))
        elif key == "exact":
            configured = bool(os.environ.get("EXACT_CLIENT_ID")
                              and os.environ.get("EXACT_CLIENT_SECRET"))
        info.append({
            "name": key,
            "display_name": display,
            "configured": configured,
            # rompslomp is in beta - werkt voor basis-flow maar nog niet 100% getest
            "implemented": key in ("moneybird", "rompslomp"),
        })
    return info


def set_active_provider(name: str):
    """Wijzig de actieve provider voor toekomstige get_provider()-calls."""
    global _active
    if name not in _PROVIDERS:
        raise ProviderError(f"Onbekende provider '{name}'")
    _active = name


def get_provider(name: str | None = None) -> AccountingProvider:
    """
    Geef de actieve provider terug. Volgorde:
      1. expliciete name argument
      2. set_active_provider() keuze tijdens runtime
      3. ACCOUNTING_PROVIDER env-variabele
      4. default 'moneybird'
    """
    chosen = name or _active or os.environ.get("ACCOUNTING_PROVIDER") or "rompslomp"
    chosen = chosen.lower().strip()
    if chosen not in _PROVIDERS:
        raise ProviderError(f"Onbekende provider '{chosen}'")

    # Cached instance? Hergebruik
    if chosen in _instances:
        return _instances[chosen]

    modpath, cls_name = _PROVIDERS[chosen]
    import importlib
    mod = importlib.import_module(modpath)
    cls = getattr(mod, cls_name)
    instance = cls()
    _instances[chosen] = instance
    return instance
