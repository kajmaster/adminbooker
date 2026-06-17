"""
accuracy_check.py - meet de boek-nauwkeurigheid voor EEN klant vóór go-live.

Draait de ECHTE pipeline (pdf_parser + classify + correctie-geheugen + LLM) over
een map met facturen van de klant, tegen hun ECHTE grootboekrekeningen (read-only
opgehaald bij de actieve provider). Er wordt NIETS geboekt - dit is puur een
meting om te beslissen of auto-boeken (>=95%) aan mag.

Rapporteert:
  - Extractie (factuurnummer/datum/bedrag) tegen een optionele waarheids-CSV.
  - Grootboek-zekerheid: welk % van de regels is de classifier zeker genoeg
    (>= DREMPEL) om automatisch te boeken, en welke moeten nagekeken.
  - Per factuur: zou deze schoon auto-geboekt worden?
  - Een go-live-oordeel + de twijfelregels die nog gekalibreerd moeten worden.

Gebruik:
    python tools/accuracy_check.py --invoices <map> [--truth <csv>]
        [--provider rompslomp] [--limit N] [--out rapport.json]

Waarheids-CSV (optioneel): kolommen pdf_file, invoice_no, issue_date, total_eur.
"""
from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from pdf_parser import parse_pdf
from classify import classificeer_grootboek, DREMPEL
import corrections
from providers import get_provider, set_active_provider


def _load_truth(path):
    truth = {}
    if not path:
        return truth
    with open(path, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            key = row.get("pdf_file") or row.get("pdf") or ""
            truth[key] = row
    return truth


def _num(x):
    try:
        return float(str(x).replace(",", "."))
    except (TypeError, ValueError):
        return None


def _is_generic(account) -> bool:
    """Verzamel-/restrekening? Daar wil je NIET blind op auto-boeken, ook al is
    de classifier 'zeker' (bv. de kale 'Kosten'-wortel of 'Diversen')."""
    import html
    naam = html.unescape(account.get("name") or "").strip().lower()
    pad = html.unescape(account.get("path_name") or "").strip().lower()
    if naam in ("kosten", "overige kosten", "diversen", "algemene kosten"):
        return True
    return any(w in pad for w in ("diversen", "algemene kosten",
                                  "overige bedrijfskosten"))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--invoices", required=True, help="map met factuur-PDF's")
    ap.add_argument("--truth", default=None, help="optionele waarheids-CSV")
    ap.add_argument("--provider", default=None, help="bv. rompslomp (anders actieve)")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--out", default=str(ROOT / "tools" / "accuracy_report.json"))
    args = ap.parse_args()

    if args.provider:
        set_active_provider(args.provider)
    provider = get_provider()
    ledgers = provider.purchase_ledgers()
    if not ledgers:
        print("Geen kosten-grootboeken bij de provider. Stop.")
        return 1
    corr = corrections.load()
    print(f"Provider: {provider.display_name} | {len(ledgers)} grootboeken | "
          f"{len(corr)} geleerde regels | drempel auto-boek: {DREMPEL}")

    truth = _load_truth(args.truth)
    pdfs = sorted(Path(args.invoices).glob("*.pdf"))
    if args.limit:
        pdfs = pdfs[: args.limit]

    facturen, twijfel = [], []
    veld_ok = {"factuurnummer": [0, 0], "datum": [0, 0], "bedrag": [0, 0]}

    for pdf in pdfs:
        parsed = parse_pdf(pdf)
        lev = (parsed.get("leverancier") or {}).get("company_name", "")
        regels_uit = []
        min_conf = 1.0
        for r in parsed.get("regels", []):
            keuze = classificeer_grootboek(
                ledgers, r.get("omschrijving", ""), leverancier=lev, corrections=corr,
            )
            conf = round(keuze.get("confidence", 0.0), 2)
            generiek = _is_generic(keuze["account"])
            # 'Zeker genoeg' = hoge zekerheid EN geen verzamelrekening.
            regel_ok = conf >= DREMPEL and not generiek
            if not regel_ok:
                min_conf = min(min_conf, conf if conf < DREMPEL else DREMPEL - 0.01)
            regel = {
                "omschrijving": r.get("omschrijving"),
                "grootboek": (keuze["account"].get("name")),
                "zekerheid": conf, "methode": keuze.get("method"),
                "verzamelrekening": generiek,
            }
            regels_uit.append(regel)
            if not regel_ok:
                twijfel.append({"pdf": pdf.name, **regel,
                                "reden": "verzamelrekening" if generiek else "lage zekerheid"})

        # Extractie scoren tegen waarheid (indien aanwezig)
        t = truth.get(pdf.name)
        if t:
            if t.get("invoice_no"):
                veld_ok["factuurnummer"][1] += 1
                veld_ok["factuurnummer"][0] += int(
                    (parsed.get("factuurnummer") or "").strip().lower()
                    == t["invoice_no"].strip().lower())
            if t.get("issue_date"):
                veld_ok["datum"][1] += 1
                veld_ok["datum"][0] += int(
                    (parsed.get("datum") or "") == t["issue_date"].strip())
            if t.get("total_eur"):
                veld_ok["bedrag"][1] += 1
                exp, got = _num(t["total_eur"]), _num(parsed.get("totaal_incl_btw"))
                veld_ok["bedrag"][0] += int(exp is not None and got is not None
                                            and abs(exp - got) < 0.02)

        facturen.append({
            "pdf": pdf.name, "leverancier": lev,
            "factuurnummer": parsed.get("factuurnummer"),
            "datum": parsed.get("datum"),
            "totaal_incl_btw": parsed.get("totaal_incl_btw"),
            "min_zekerheid": round(min_conf, 2),
            "auto_boekbaar": bool(parsed.get("regels")) and min_conf >= DREMPEL,
            "regels": regels_uit,
        })

    n = len(facturen)
    auto = sum(1 for f in facturen if f["auto_boekbaar"])
    n_regels = sum(len(f["regels"]) for f in facturen)
    zeker_regels = n_regels - len(twijfel)

    rapport = {
        "provider": provider.name, "aantal_facturen": n,
        "auto_boekbaar": auto,
        "auto_boekbaar_pct": round(auto / n * 100, 1) if n else 0,
        "grootboek_zeker_regels_pct": round(zeker_regels / n_regels * 100, 1) if n_regels else 0,
        "extractie_pct": {
            k: round(v[0] / v[1] * 100, 1) for k, v in veld_ok.items() if v[1]
        },
        "te_kalibreren": twijfel[:50],
        "facturen": facturen,
    }
    Path(args.out).write_text(json.dumps(rapport, indent=2, ensure_ascii=False), encoding="utf-8")

    print("=" * 60)
    print(f"Facturen getest:          {n}")
    if rapport["extractie_pct"]:
        for k, p in rapport["extractie_pct"].items():
            print(f"  extractie {k:13s} {p:5.1f}%")
    print(f"Grootboek zeker (regels): {rapport['grootboek_zeker_regels_pct']}%")
    print(f"Auto-boekbaar (facturen): {auto}/{n}  ({rapport['auto_boekbaar_pct']}%)")
    gate = rapport["auto_boekbaar_pct"] >= 95
    print(f"\nGO-LIVE (>=95% auto-boekbaar): {'JA' if gate else 'NEE - eerst kalibreren'}")
    if twijfel:
        print(f"\n{len(twijfel)} twijfelregel(s) om te kalibreren (top 10):")
        for t in twijfel[:10]:
            print(f"  {t['pdf']}: {t['omschrijving']!r} -> {t['grootboek']} "
                  f"(zekerheid {t['zekerheid']}, {t['methode']})")
    print(f"\nRapport: {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
