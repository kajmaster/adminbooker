"""
ijk.py - leer van de eigen boekhouding ("ijk-import").

Doel: bij het onboarden van een nieuwe klant hoeven ze geen facturen aan te
leveren of te anonimiseren. Hun boekhoudpakket bevat al maanden correct
geboekte inkoopfacturen: per regel staat er een omschrijving + het grootboek
dat hun boekhouder koos. Dat is precies de gelabelde data die de classifier
nodig heeft.

Deze module haalt die geboekte inkoopfacturen op via de actieve provider en
zet per regel (omschrijving -> grootboek) een entry in het correctie-geheugen
(corrections.py). Bij een volgende, vrijwel identieke regel kiest de classifier
dat grootboek dan deterministisch met hoge zekerheid.

Privacy: de data blijft in de instance van de klant. We bewaren alleen de
regel-omschrijving, een hint over de leverancier en het grootboek - geen
bedragen, geen klantnamen-van-klanten, geen PDF's.

Gebruik (CLI):
    python ijk.py                 # importeer uit de actieve provider
    python ijk.py --dry-run       # toon wat er geleerd zou worden, schrijf niets
    python ijk.py --limit 50      # beperk tot 50 facturen
    python ijk.py --provider rompslomp

Gebruik (vanuit de app): POST /api/ijk/import  (zie app.py)
"""
from __future__ import annotations

import argparse
import sys

import corrections


# Mogelijke veldnamen waar omschrijving / grootboek / leverancier kunnen staan.
# Defensief, omdat de exacte API-vorm per provider en endpoint verschilt.
_LINE_KEYS = ("invoice_lines", "details", "details_attributes",
              "expense_details", "lines")
_DESC_KEYS = ("description", "omschrijving", "name", "text")
_ACCOUNT_KEYS = ("ledger_account_id", "account_id", "type_account_id",
                 "ledger_id", "grootboek_id")
_SUPPLIER_KEYS = ("contact_name", "relation_name", "supplier_name",
                  "company_name", "naam")

# Omschrijvingen die te generiek zijn om iets van te leren.
_SKIP_DESC = {"", "regel", "diversen", "kosten", "factuur", "div", "-"}


def _first(d: dict, keys) -> object:
    for k in keys:
        if isinstance(d, dict) and d.get(k) not in (None, ""):
            return d[k]
    return None


def _unwrap(item, *wrappers):
    """Sommige Rompslomp-responses wikkelen objecten in {'expense': {...}}."""
    if isinstance(item, dict):
        for w in wrappers:
            if w in item and isinstance(item[w], dict):
                return item[w]
    return item


def _ledger_name_map(provider) -> dict:
    """id(str) -> nette grootboeknaam, uit de kosten-grootboeken."""
    import html as _html
    out = {}
    try:
        for l in provider.purchase_ledgers() or []:
            lid = l.get("id")
            if lid is None:
                continue
            name = _html.unescape(l.get("path_name") or l.get("name") or "")
            out[str(lid)] = name
    except Exception:
        pass
    return out


def _supplier_name(expense: dict) -> str:
    # Direct veld?
    direct = _first(expense, _SUPPLIER_KEYS)
    if direct:
        return str(direct)
    # Genest contact-object?
    contact = expense.get("contact") or expense.get("cached_contact") or {}
    if isinstance(contact, dict):
        return str(contact.get("company_name")
                   or contact.get("contact_person_name")
                   or contact.get("name") or "")
    return ""


def _lines_of(expense: dict, provider) -> list:
    """Haal de regels van een expense. Eerst uit het object zelf; ontbreken ze,
    dan via de detail-call van de provider (indien beschikbaar)."""
    for lk in _LINE_KEYS:
        if isinstance(expense.get(lk), list) and expense[lk]:
            return expense[lk]
    # Detail ophalen
    getter = getattr(provider, "get_purchase_invoice", None)
    eid = expense.get("id")
    if callable(getter) and eid is not None:
        try:
            detail = _unwrap(getter(eid), "expense")
            for lk in _LINE_KEYS:
                if isinstance(detail.get(lk), list) and detail[lk]:
                    # neem ook supplier mee uit detail als die er is
                    if not _supplier_name(expense):
                        expense["__supplier_detail"] = _supplier_name(detail)
                    return detail[lk]
        except Exception:
            return []
    return []


def seed_from_provider(provider, limit=None, dry_run=False) -> dict:
    """Lees geboekte inkoopfacturen en zet per regel (omschrijving -> grootboek)
    in het correctie-geheugen. Retourneert een samenvatting."""
    ledger_names = _ledger_name_map(provider)

    # Lijst van geboekte inkoopfacturen ophalen (met fallback op naamvariant).
    expenses = []
    for meth in ("list_purchase_invoices", "list_open_purchase_invoices"):
        fn = getattr(provider, meth, None)
        if callable(fn):
            try:
                expenses = fn() or []
            except Exception:
                expenses = []
            if expenses:
                break

    summary = {
        "facturen": 0, "regels": 0, "geleerd": 0,
        "overgeslagen": 0, "voorbeelden": [], "dry_run": bool(dry_run),
    }

    for raw in expenses:
        expense = _unwrap(raw, "expense")
        if not isinstance(expense, dict):
            continue
        summary["facturen"] += 1
        if limit and summary["facturen"] > limit:
            summary["facturen"] = limit
            break

        leverancier = _supplier_name(expense)
        for line in _lines_of(expense, provider):
            if not isinstance(line, dict):
                continue
            summary["regels"] += 1
            desc = _first(line, _DESC_KEYS)
            acc = _first(line, _ACCOUNT_KEYS)
            desc_str = str(desc).strip() if desc is not None else ""
            if not desc_str or desc_str.lower() in _SKIP_DESC or acc is None:
                summary["overgeslagen"] += 1
                continue
            lev = leverancier or expense.get("__supplier_detail") or ""
            acc_name = ledger_names.get(str(acc), "")
            if len(summary["voorbeelden"]) < 10:
                summary["voorbeelden"].append({
                    "omschrijving": desc_str,
                    "grootboek": acc_name or str(acc),
                    "leverancier": lev,
                })
            if not dry_run:
                try:
                    corrections.add(desc_str, lev, acc, acc_name)
                except Exception:
                    summary["overgeslagen"] += 1
                    continue
            summary["geleerd"] += 1

    return summary


def main(argv=None):
    ap = argparse.ArgumentParser(description="Leer van de eigen boekhouding.")
    ap.add_argument("--provider", default=None,
                    help="provider-naam (default: actieve provider)")
    ap.add_argument("--limit", type=int, default=None,
                    help="max aantal facturen")
    ap.add_argument("--dry-run", action="store_true",
                    help="toon resultaat, schrijf niets weg")
    args = ap.parse_args(argv)

    from providers import get_provider, set_active_provider
    if args.provider:
        set_active_provider(args.provider)
    provider = get_provider()

    print(f"Ijk-import via provider: {provider.display_name}")
    summary = seed_from_provider(provider, limit=args.limit, dry_run=args.dry_run)
    print(f"  Facturen bekeken : {summary['facturen']}")
    print(f"  Regels gezien    : {summary['regels']}")
    print(f"  Geleerd          : {summary['geleerd']}"
          + ("  (dry-run: niets weggeschreven)" if summary["dry_run"] else ""))
    print(f"  Overgeslagen     : {summary['overgeslagen']}")
    if summary["voorbeelden"]:
        print("  Voorbeelden:")
        for v in summary["voorbeelden"]:
            lev = f" [{v['leverancier']}]" if v["leverancier"] else ""
            print(f"    - {v['omschrijving']!r} -> {v['grootboek']}{lev}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
