"""
evaluator.py - vergelijkt extracted JSON met ground truth en scoort.

Per factuur worden de volgende velden gescoord:
  - factuurnummer
  - datum
  - vervaldatum
  - leverancier.company_name
  - leverancier.tax_number, chamber_of_commerce, iban (optioneel)
  - aantal regels (klopt het?)
  - per regel: omschrijving, aantal, prijs, BTW%
  - totalen excl/incl BTW

Output:
  - JSON-rapport met per-factuur details
  - Markdown-samenvatting voor de gebruiker
"""
from __future__ import annotations

import json
from pathlib import Path

HERE = Path(__file__).resolve().parent
DATASET = HERE / "dataset"
EXTRACTED = HERE / "extracted"
EXTRACTED.mkdir(exist_ok=True)


def normalize_str(s):
    if s is None:
        return ""
    return str(s).strip().lower().replace("  ", " ")


def normalize_iban(s):
    if not s:
        return ""
    return str(s).replace(" ", "").upper()


def date_match(a, b):
    """Datums kunnen verschillen in formaat. Vergelijk gewoon op string."""
    if not a or not b:
        return a == b
    return str(a).strip() == str(b).strip()


def num_close(a, b, tol=0.01):
    if a is None and b is None:
        return True
    if a is None or b is None:
        return False
    try:
        return abs(float(a) - float(b)) < tol
    except (TypeError, ValueError):
        return False


def score_factuur(extracted, truth):
    """Score één factuur. Retourneer dict met per-veld correct (bool) + reden."""
    s = {}

    # 1. Factuurnummer
    s["factuurnummer"] = (
        normalize_str(extracted.get("factuurnummer")) ==
        normalize_str(truth["factuurnummer"])
    )

    # 2. Datum
    s["datum"] = date_match(extracted.get("datum"), truth["datum"])

    # 3. Vervaldatum
    s["vervaldatum"] = date_match(
        extracted.get("vervaldatum"), truth.get("vervaldatum")
    )

    # 4. Leverancier - company_name
    e_lev = extracted.get("leverancier") or {}
    t_lev = truth["leverancier"]
    s["leverancier_naam"] = (
        normalize_str(e_lev.get("company_name")) ==
        normalize_str(t_lev.get("company_name"))
    )

    # 5. Leverancier - btw nummer (alleen scoren als truth het heeft)
    if t_lev.get("tax_number"):
        s["leverancier_btw"] = (
            normalize_str(e_lev.get("tax_number")) ==
            normalize_str(t_lev.get("tax_number"))
        )
    if t_lev.get("chamber_of_commerce"):
        s["leverancier_kvk"] = (
            normalize_str(e_lev.get("chamber_of_commerce")) ==
            normalize_str(t_lev.get("chamber_of_commerce"))
        )
    if t_lev.get("iban"):
        s["leverancier_iban"] = (
            normalize_iban(e_lev.get("iban")) ==
            normalize_iban(t_lev.get("iban"))
        )

    # 6. Aantal regels
    e_reg = extracted.get("regels", [])
    t_reg = truth["regels"]
    s["aantal_regels"] = len(e_reg) == len(t_reg)

    # 7. Per regel scoren (alleen als aantal klopt)
    if s["aantal_regels"]:
        regel_correct = 0
        for er, tr in zip(e_reg, t_reg):
            ok_om = normalize_str(er.get("omschrijving")) == normalize_str(tr["omschrijving"])
            ok_aa = num_close(er.get("aantal"), tr["aantal"])
            ok_pr = num_close(er.get("prijs_per_stuk"), tr["prijs_per_stuk"])
            t_pct = tr.get("btw_percentage")
            e_pct = er.get("btw_percentage")
            if t_pct is None:
                ok_btw = e_pct is None or (isinstance(e_pct, (int, float)) and float(e_pct) == 0)
            else:
                ok_btw = num_close(e_pct, t_pct)
            if ok_om and ok_aa and ok_pr and ok_btw:
                regel_correct += 1
        s["regels_correct"] = regel_correct == len(t_reg)
        s["regel_detail"] = f"{regel_correct}/{len(t_reg)} regels exact correct"

    # 8. Totaal-sanity (subtotaal, btw, totaal kloppen?)
    # Als de extractie deze velden niet gaf, sla over
    if "totaal_excl_btw" in extracted or "totaal_incl_btw" in extracted:
        sub_truth = sum(r["aantal"] * r["prijs_per_stuk"] for r in t_reg)
        btw_truth = sum(
            r["aantal"] * r["prijs_per_stuk"] * ((r.get("btw_percentage") or 0) / 100)
            for r in t_reg
        )
        if truth.get("btw_verlegd"):
            btw_truth = 0
        tot_truth = sub_truth + btw_truth

        s["totaal_excl"] = num_close(extracted.get("totaal_excl_btw"), sub_truth, tol=0.05)
        s["totaal_incl"] = num_close(extracted.get("totaal_incl_btw"), tot_truth, tol=0.05)

    return s


def overall(scores_per_factuur):
    """Aggregeer scores. Een factuur is 'correct' als alle velden goed zijn."""
    n = len(scores_per_factuur)
    field_totals = {}
    correct_facturen = 0
    for fac in scores_per_factuur:
        all_ok = True
        for k, v in fac["fields"].items():
            if k.endswith("_detail"):
                continue
            field_totals.setdefault(k, [0, 0])
            field_totals[k][1] += 1
            if v is True:
                field_totals[k][0] += 1
            else:
                all_ok = False
        if all_ok:
            correct_facturen += 1

    field_pct = {
        k: (got / total * 100) if total else 0.0
        for k, (got, total) in field_totals.items()
    }
    return {
        "totaal_facturen": n,
        "volledig_correct": correct_facturen,
        "volledig_correct_pct": correct_facturen / n * 100 if n else 0,
        "veld_accuracy_pct": field_pct,
    }


def main():
    pdfs = sorted(DATASET.glob("factuur_*.pdf"))
    results = []
    for pdf in pdfs:
        name = pdf.stem  # factuur_01
        truth_path = DATASET / f"{name}.truth.json"
        ext_path = EXTRACTED / f"{name}.extracted.json"
        if not ext_path.exists():
            print(f"!! {name}: geen extractie gevonden, sla over")
            continue
        truth = json.loads(truth_path.read_text(encoding="utf-8"))
        extracted = json.loads(ext_path.read_text(encoding="utf-8"))
        s = score_factuur(extracted, truth)
        results.append({"factuur": name, "fields": s})

    summary = overall(results)
    rapport = {
        "samenvatting": summary,
        "per_factuur": results,
    }
    rapport_path = HERE / "accuracy_rapport.json"
    rapport_path.write_text(
        json.dumps(rapport, indent=2, ensure_ascii=False), encoding="utf-8"
    )

    print("=" * 60)
    print(" ACCURACY RAPPORT")
    print("=" * 60)
    print(f"Totaal facturen getest:        {summary['totaal_facturen']}")
    print(f"Volledig 100% correct:         {summary['volledig_correct']}/"
          f"{summary['totaal_facturen']}  "
          f"({summary['volledig_correct_pct']:.1f}%)")
    print()
    print("Accuracy per veld:")
    for veld, pct in sorted(summary["veld_accuracy_pct"].items(), key=lambda x: -x[1]):
        bar = "#" * int(pct / 5)
        print(f"  {veld:25s} {pct:5.1f}%  {bar}")
    print()
    print("Per factuur (compact):")
    for r in results:
        ok = sum(1 for v in r["fields"].values() if v is True)
        tot = sum(1 for k in r["fields"] if not k.endswith("_detail"))
        bad = [k for k, v in r["fields"].items() if v is False]
        marker = "OK" if not bad else "FOUT"
        print(f"  {r['factuur']}: {marker} ({ok}/{tot})  fout: {', '.join(bad) if bad else '-'}")

    print()
    print(f"Volledig rapport: {rapport_path}")


if __name__ == "__main__":
    main()
