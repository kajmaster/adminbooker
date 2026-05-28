"""
bank_agent.py - importeer bankbestand in Moneybird + match openstaande facturen.

Stappen:
1. parse_bank_file() -> lijst transacties
2. importeer als financial_statement in Moneybird (mutaties worden aangemaakt)
3. haal openstaande inkoop- + verkoopfacturen op
4. per mutatie: score elke factuur, koppel beste match boven threshold
"""
from __future__ import annotations

import re
from pathlib import Path

from providers import get_provider
from bank_parser import parse_bank_file


# Drempel voor automatische match - onder deze score zetten we 'm in de
# 'onbekend' stapel (gebruiker moet handmatig matchen in Moneybird)
MATCH_THRESHOLD = 60


def _normalize_iban(s):
    if not s:
        return ""
    return str(s).upper().replace(" ", "")


def _normalize_text(s):
    if not s:
        return ""
    return re.sub(r"[\s\-/\.]+", "", str(s).lower())


def score_match(mutatie, factuur, factuur_type):
    """
    Score hoe goed een mutatie bij een factuur past (0-100+).
    Hogere score = betere match.

    factuur_type: 'inkoop' of 'verkoop' - bepaalt of we negatief
    of positief bedrag verwachten.
    """
    score = 0
    reasons = []

    # 1. Bedrag (belangrijkste, max 50)
    mut_amount = abs(mutatie["bedrag"])
    fac_amount = abs(float(factuur.get("total_price_incl_tax", 0) or 0))
    if mut_amount > 0 and fac_amount > 0:
        if abs(mut_amount - fac_amount) < 0.01:
            score += 50
            reasons.append("bedrag exact")
        elif abs(mut_amount - fac_amount) / fac_amount < 0.02:
            score += 30
            reasons.append("bedrag bijna exact")

    # 2. Richting: inkoop = uitgaand (negatief), verkoop = inkomend (positief)
    if factuur_type == "inkoop" and mutatie["bedrag"] < 0:
        score += 5
    elif factuur_type == "verkoop" and mutatie["bedrag"] > 0:
        score += 5
    elif factuur_type == "inkoop" and mutatie["bedrag"] > 0:
        score -= 30  # waarschijnlijk verkeerde kant
    elif factuur_type == "verkoop" and mutatie["bedrag"] < 0:
        score -= 30

    # 3. IBAN tegenrekening
    mut_iban = _normalize_iban(mutatie.get("tegen_iban"))
    contact_iban = _normalize_iban((factuur.get("contact") or {}).get("sepa_iban"))
    if mut_iban and contact_iban and mut_iban == contact_iban:
        score += 25
        reasons.append("IBAN match")

    # 4. Factuurnummer in omschrijving
    ref = factuur.get("reference") or factuur.get("invoice_id") or ""
    if ref:
        ref_norm = _normalize_text(ref)
        msg_norm = _normalize_text(mutatie.get("omschrijving"))
        if ref_norm and len(ref_norm) >= 3 and ref_norm in msg_norm:
            score += 30
            reasons.append(f"factuurnr '{ref}' in omschrijving")

    # 5. Naam tegenrekening matcht bedrijfsnaam
    contact_name = ((factuur.get("contact") or {}).get("company_name") or "").lower()
    mut_name = (mutatie.get("tegen_naam") or "").lower()
    if contact_name and mut_name and (contact_name in mut_name or mut_name in contact_name):
        score += 10
        reasons.append("naam match")

    return score, reasons


def match_mutations(mb, mutations_from_mb):
    """
    Voor elke onmatched mutation uit Moneybird, score tegen alle openstaande
    facturen (inkoop EN verkoop). Koppel beste match boven threshold.

    Retourneert lijst resultaten per mutation.
    """
    print(">> [Bank] Openstaande facturen ophalen...")
    open_purchase = mb.list_open_purchase_invoices()
    open_sales = mb.list_open_sales_invoices()
    print(f"   {len(open_purchase)} inkoop + {len(open_sales)} verkoop = "
          f"{len(open_purchase) + len(open_sales)} openstaande facturen")

    results = []
    for mut in mutations_from_mb:
        if mut.get("processed") or mut.get("fully_matched"):
            results.append({
                "mutation_id": mut["id"],
                "status": "al gematcht",
                "matched_factuur": None,
                "score": None,
            })
            continue

        mut_data = {
            "bedrag": float(mut.get("amount", 0)),
            "datum": mut.get("date"),
            "omschrijving": mut.get("message", ""),
            "tegen_iban": mut.get("contra_account_number") or mut.get("account_iban"),
            "tegen_naam": mut.get("contra_account_name"),
        }

        beste = None
        beste_score = 0
        for fac in open_purchase:
            score, reasons = score_match(mut_data, fac, "inkoop")
            if score > beste_score:
                beste_score = score
                beste = ("inkoop", fac, reasons)
        for fac in open_sales:
            score, reasons = score_match(mut_data, fac, "verkoop")
            if score > beste_score:
                beste_score = score
                beste = ("verkoop", fac, reasons)

        if beste and beste_score >= MATCH_THRESHOLD:
            f_type, fac, reasons = beste
            try:
                # booking_type 'Document' werkt voor purchase invoices
                # 'SalesInvoice' werkt voor sales invoices
                booking_type = "Document" if f_type == "inkoop" else "SalesInvoice"
                mb.link_mutation_to_booking(
                    mutation_id=mut["id"],
                    booking_type=booking_type,
                    booking_id=fac["id"],
                    price=mut_data["bedrag"],
                )
                results.append({
                    "mutation_id": mut["id"],
                    "datum": mut_data["datum"],
                    "bedrag": mut_data["bedrag"],
                    "omschrijving": mut_data["omschrijving"][:80],
                    "status": "gekoppeld",
                    "matched_factuur": fac.get("reference") or fac.get("invoice_id"),
                    "factuur_type": f_type,
                    "score": beste_score,
                    "reasons": reasons,
                })
                print(f"   ✓ {mut_data['datum']} EUR {mut_data['bedrag']:>8.2f} "
                      f"-> {f_type} factuur {fac.get('reference')} "
                      f"(score {beste_score})")
            except Exception as e:
                results.append({
                    "mutation_id": mut["id"],
                    "datum": mut_data["datum"],
                    "bedrag": mut_data["bedrag"],
                    "status": "koppeling mislukt",
                    "error": str(e),
                    "score": beste_score,
                })
                print(f"   ! koppeling mislukt voor {mut['id']}: {e}")
        else:
            results.append({
                "mutation_id": mut["id"],
                "datum": mut_data["datum"],
                "bedrag": mut_data["bedrag"],
                "omschrijving": mut_data["omschrijving"][:80],
                "status": "geen match",
                "beste_score": beste_score,
            })
            print(f"   ? {mut_data['datum']} EUR {mut_data['bedrag']:>8.2f} "
                  f"-> geen match (beste score {beste_score})")

    return results


def import_en_match(bank_file_path, financial_account_id=None,
                     reference=None):
    """
    Hoofdfunctie: lees bankbestand, importeer in Moneybird, match alle
    nieuwe mutaties met openstaande facturen.

    Retourneert dict met statement, mutations, match_results.
    """
    mb = get_provider()

    print(">> [Bank] Stap 1: Bankbestand parsen...")
    transacties = parse_bank_file(bank_file_path)
    if not transacties:
        raise RuntimeError(
            "Geen transacties gevonden in bankbestand. "
            "Controleer formaat (CSV/MT940)."
        )
    print(f"   {len(transacties)} transacties gevonden")

    print(">> [Bank] Stap 2: Bankrekening selecteren...")
    if financial_account_id is None:
        acct = mb.primary_financial_account()
        if not acct:
            raise RuntimeError(
                "Geen bankrekening gevonden in Moneybird. "
                "Maak er eerst eentje aan via Instellingen > Bankrekeningen."
            )
        financial_account_id = acct["id"]
        print(f"   Gebruikt: {acct.get('name')} ({acct.get('iban')})")

    print(">> [Bank] Stap 3: Mutaties importeren in Moneybird...")
    mutations_payload = []
    for t in transacties:
        mutations_payload.append({
            "date": t["datum"],
            "amount": t["bedrag"],
            "message": t["omschrijving"],
            "contra_account_iban": t.get("tegen_iban"),
            "contra_account_name": t.get("tegen_naam"),
        })

    ref = reference or f"AdminBoeker import {Path(bank_file_path).name}"
    statement = mb.create_financial_statement(
        financial_account_id=financial_account_id,
        reference=ref,
        mutations=mutations_payload,
    )
    nieuwe_mutaties = statement.get("financial_mutations", [])
    print(f"   Statement {statement.get('id')} aangemaakt met "
          f"{len(nieuwe_mutaties)} mutaties")

    print(">> [Bank] Stap 4: Matchen met openstaande facturen...")
    match_results = match_mutations(mb, nieuwe_mutaties)

    gekoppeld = sum(1 for r in match_results if r.get("status") == "gekoppeld")
    geen_match = sum(1 for r in match_results if r.get("status") == "geen match")
    print()
    print("=" * 60)
    print(f"  Klaar: {gekoppeld} gekoppeld, {geen_match} geen match")
    print("=" * 60)

    return {
        "statement_id": statement.get("id"),
        "totaal_mutaties": len(nieuwe_mutaties),
        "gekoppeld": gekoppeld,
        "geen_match": geen_match,
        "details": match_results,
    }


if __name__ == "__main__":
    import json, sys
    if len(sys.argv) != 2:
        print("usage: python bank_agent.py /pad/naar/bankbestand.csv")
        sys.exit(2)
    result = import_en_match(sys.argv[1])
    print(json.dumps(result, indent=2, ensure_ascii=False))
