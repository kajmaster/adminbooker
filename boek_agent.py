"""
boek_agent.py - boek 1 inkoopfactuur volledig automatisch in Moneybird.

Gebruik:
    python boek_agent.py factuur_data.json /pad/naar/factuur.pdf
"""

from __future__ import annotations

import html
import json
import sys
from pathlib import Path

from providers import get_provider, AccountingProvider
import corrections


# ---------- slimme grootboek-keuze ----------

# Trefwoorden in de factuurregel/leverancier -> kandidaat-grootboeken
# (gezocht als substring in de volledige grootboeknaam/path, in voorkeursvolgorde).
# Werkt over pakketten heen: we matchen op herkenbare woorden in de naam, niet op
# een vast ID. Bouw-relevante categorieen staan bewust bovenaan.
_GROOTBOEK_REGELS = [
    (["onderaannemer", "onderaanneming", "uitbesteed", "uitbesteding",
      "werk door derden", "inhuur", "ingehuurd", "zzp", "freelance",
      "freelancer", "detachering", "manuren derden",
      "consultancy", "consulting", "consultant", "advies", "adviseur",
      "advisering", "interim", "strategie", "strategisch"],
     ["werk door derden"]),
    (["gereedschap", "machine", "machines", "boormachine", "zaag", "kraan",
      "steiger", "heftruck", "aggregaat", "compressor"],
     ["gereedschap", "machines"]),
    (["bouwmateriaal", "bouwmaterialen", "hout", "beton", "cement", "zand",
      "stenen", "baksteen", "bakstenen", "schroef", "schroeven", "spijkers",
      "isolatie", "tegels", "verf", "kit", "gips", "staal", "dakbedekking"],
     ["klein materiaal", "inkoop voorraad", "onderhoud"]),
    (["onderhoud", "reparatie", "reparaties", "service", "storing", "monteur"],
     ["onderhoud", "reparaties"]),
    (["kleding", "werkkleding", "veiligheidsschoenen", "werkschoenen",
      "overall", "helm", "handschoenen", "bedrijfskleding", "veiligheidshesje"],
     ["werkkleding"]),
    (["seo", "marketing", "advertentie", "advertenties", "reclame",
      "google ads", "facebook ads", "adwords", "campagne", "social media",
      "flyer", "drukwerk", "website", "logo", "huisstijl",
      "content", "optimization", "optimalisatie", "conversie", "copywriting",
      "nieuwsbrief", "branding"],
     ["marketing", "reclame", "verkoopkosten"]),
    (["telefoon", "mobiel", "internet", "kpn", "vodafone", "ziggo", "t-mobile",
      "odido", "simkaart", "glasvezel"],
     ["telefoon", "internet"]),
    (["software", "saas", "licentie", "licenties", "hosting", "domein",
      "domain", "microsoft", "adobe", "google workspace", "office 365",
      "abonnement", "cloud", "subscription", "automation", "automatisering",
      "automatiseren", "integratie", "integration", "api", "development",
      "developer", "ontwikkeling", "webdevelopment", "platform", "dashboard"],
     ["abonnementen", "overige kosten"]),
    (["opleiding", "cursus", "training", "seminar", "workshop", "certificering",
      "examen", "diploma", "vca"],
     ["opleiding"]),
    (["vakliteratuur", "vakblad", "tijdschrift", "literatuur"],
     ["vakliteratuur"]),
    (["kantoorartikel", "kantoorartikelen", "papier", "printer", "inkt",
      "toner", "pennen", "nietmachine", "ordner", "kantoorbenodigdheden"],
     ["kantoorartikelen"]),
    (["administratie", "boekhouding", "boekhouder", "accountant",
      "jaarrekening", "belastingaangifte", "salarisadministratie", "notaris",
      "juridisch", "advocaat"],
     ["administratiekosten"]),
    (["kilometervergoeding", "km vergoeding", "reiskosten", "trein",
      "openbaar vervoer", "ns "],
     ["kilometervergoeding", "auto- en transport"]),
    (["brandstof", "benzine", "diesel", "tankstation", "tanken", "shell",
      "esso", "parkeren", "parkeerkosten", "tol", "wegenbelasting", "lease",
      "leasing", "autoverzekering", "apk", "auto", "bestelbus", "bus"],
     ["autokosten", "auto- en transport"]),
    (["huur", "huurpand", "huisvesting", "werkruimte", "kantoorruimte",
      "energie", "elektra", "elektriciteit", "stroom", "water", "eneco",
      "vattenfall", "essent", "gas"],
     ["werkruimte", "huisvesting"]),
    (["representatie", "lunch", "diner", "restaurant", "horeca", "etentje",
      "catering", "borrel"],
     ["representatie", "verkoopkosten"]),
    (["relatiegeschenk", "relatiegeschenken", "cadeau", "geschenk", "bloemen",
      "kerstpakket"],
     ["relatiegeschenken"]),
    (["rente", "bankkosten", "transactiekosten", "incassokosten"],
     ["rente"]),
    (["voorraad", "grondstof", "grondstoffen", "handelsgoederen"],
     ["inkoop voorraad"]),
    (["afschrijving", "afschrijvingen"],
     ["afschrijvingen"]),
]


def _ledger_tekst(l):
    """Volledige, leesbare naam van een grootboek (path heeft voorkeur)."""
    return html.unescape(l.get("path_name") or l.get("name") or "").lower()


# ---------- helpers ----------

def kies_grootboek(ledgers, hint, omschrijving="", leverancier=""):
    """Kies een kosten-grootboek op basis van regelinhoud + leverancier.

    Volgorde:
      1. trefwoord-regels (slim, pakket-onafhankelijk)
      2. directe naam-match op een expliciete hint
      3. zinnige default ('Diversen' / 'Overige kosten' / 'Algemene kosten')
      4. Moneybird account_type-fallback
      5. laatste redmiddel: vermijd 'Betalingsverschillen' en 'Rente'
    """
    if not ledgers:
        raise RuntimeError(
            "Geen inkoop-grootboekrekeningen beschikbaar in dit boekhoudpakket. "
            "Controleer of de administratie grootboekrekeningen heeft en of de "
            "API-functie geactiveerd is."
        )

    context = " ".join(
        str(x) for x in (hint, omschrijving, leverancier) if x
    ).lower().strip()

    # 1. trefwoord-regels
    if context:
        for trefwoorden, patronen in _GROOTBOEK_REGELS:
            if any(tw in context for tw in trefwoorden):
                for patroon in patronen:
                    for l in ledgers:
                        if patroon in _ledger_tekst(l):
                            return l
                # regel matchte maar geen passend grootboek: blijf zoeken

    # 2. directe naam-match op een expliciete hint
    if hint:
        h = hint.lower().strip()
        for l in ledgers:
            naam = html.unescape(l.get("name") or "").lower()
            if naam and len(naam) > 3 and (h in naam or naam in h):
                return l

    # 3. zinnige default i.p.v. de eerste-de-beste rekening
    for patroon in ("diversen", "overige kosten", "algemene kosten",
                    "overige bedrijfskosten"):
        for l in ledgers:
            if patroon in _ledger_tekst(l):
                return l

    # 4. Moneybird-stijl account_type
    expenses = [l for l in ledgers if l.get("account_type") == "expenses"]
    if expenses:
        return expenses[0]

    # 5. laatste redmiddel: niet op een 'verschillen'/'rente'-rekening boeken
    for l in ledgers:
        t = _ledger_tekst(l)
        if "betalingsverschillen" not in t and "verschillen" not in t \
                and "rente" not in t:
            return l
    return ledgers[0]


def vind_of_maak_leverancier(mb, lev):
    """Probeer leverancier te vinden op company_name, anders aanmaken.

    Markeert het teruggegeven contact met `_nieuw_aangemaakt` (bool) zodat de
    aanroeper (o.a. de sandbox) kan tonen of er een nieuw contact is gemaakt of
    een bestaand contact is hergebruikt.
    """
    naam = lev.get("company_name") or lev.get("firstname") or ""
    bestaand = mb.find_contact(naam) if naam else None
    if bestaand:
        if isinstance(bestaand, dict):
            bestaand["_nieuw_aangemaakt"] = False
        return bestaand

    payload = {
        "company_name": lev.get("company_name"),
        "firstname": lev.get("firstname"),
        "lastname": lev.get("lastname"),
        "address1": lev.get("address1"),
        "address2": lev.get("address2"),
        "zipcode": lev.get("zipcode"),
        "city": lev.get("city"),
        "country": lev.get("country", "NL"),
        "phone": lev.get("phone"),
        "email": lev.get("email"),
        "tax_number": lev.get("tax_number"),
        "chamber_of_commerce": lev.get("chamber_of_commerce"),
        "supplier": True,
    }
    if lev.get("iban"):
        payload["sepa_iban"] = lev["iban"]
        if lev.get("company_name"):
            payload["sepa_iban_account_name"] = lev["company_name"]

    payload = {k: v for k, v in payload.items() if v not in (None, "")}
    nieuw = mb.create_contact(payload)
    if isinstance(nieuw, dict):
        nieuw["_nieuw_aangemaakt"] = True
    return nieuw


# ---------- main ----------

def boek(data, pdf_path):
    """Voer de daadwerkelijke boeking uit. Retourneer Moneybird-respons."""
    mb = get_provider()

    print(">> Stap 1: Leverancier ophalen of aanmaken...")
    contact = vind_of_maak_leverancier(mb, data["leverancier"])
    print(f"   contact_id={contact['id']} ({contact.get('company_name')})")

    print(">> Stap 2: Grootboekrekeningen ophalen...")
    ledgers = mb.purchase_ledgers()
    print(f"   {len(ledgers)} bruikbare grootboekrekeningen")

    print(">> Stap 3: Factuurregels samenstellen...")
    lev = data.get("leverancier", {}) or {}
    lev_naam = lev.get("company_name") or lev.get("firstname") or ""

    # Classifier met zekerheid (correctie-geheugen + slimme regels). Lazy import
    # om circulaire import te voorkomen (classify importeert uit boek_agent).
    from classify import classificeer_grootboek, DREMPEL
    corr = corrections.load()

    details = []
    trace_regels = []
    fallback_gebruikt = False
    prices_incl_tax_override = None
    for r in data["regels"]:
        keuze = classificeer_grootboek(
            ledgers,
            r.get("omschrijving", ""),
            leverancier=lev_naam,
            hint=r.get("grootboek_hint"),
            corrections=corr,
        )
        ledger = keuze["account"]
        gevraagd_pct = r.get("btw_percentage")
        tax = mb.find_or_create_tax_rate(
            gevraagd_pct, tax_rate_type="purchase_invoice"
        )
        is_vrijgesteld = tax.get("percentage") is None
        if gevraagd_pct not in (None, 0) and is_vrijgesteld:
            fallback_gebruikt = True

        aantal = int(r.get("aantal", 1))
        if gevraagd_pct not in (None, 0) and is_vrijgesteld:
            prijs = float(r["prijs_per_stuk"]) * (1 + float(gevraagd_pct) / 100)
            prices_incl_tax_override = True
        else:
            prijs = float(r["prijs_per_stuk"])

        details.append({
            "description": r["omschrijving"],
            "amount": f"{aantal} x",
            "price": f"{prijs:.2f}",
            "tax_rate_id": tax["id"],
            "ledger_account_id": ledger["id"],
        })
        trace_regels.append({
            "omschrijving": r["omschrijving"],
            "aantal": aantal,
            "prijs_per_stuk": prijs,
            "btw_pct_gevraagd": gevraagd_pct,
            "btw_pct_geboekt": tax.get("percentage"),
            "btw_naam": tax.get("name"),
            "grootboek_id": ledger.get("id"),
            "grootboek_naam": html.unescape(ledger.get("name") or ""),
            "grootboek_path": html.unescape(
                ledger.get("path_name") or ledger.get("name") or ""
            ),
            "zekerheid": round(keuze.get("confidence", 0.0), 2),
            "zekerheid_methode": keuze.get("method"),
            "zekerheid_reden": keuze.get("reason"),
            "alternatieven": keuze.get("alternatives") or [],
        })
        print(
            f"   regel: {r['omschrijving'][:40]:40s} | "
            f"EUR {prijs:.2f} x {aantal} | "
            f"BTW {gevraagd_pct}% -> {tax['name']} | "
            f"grootboek={ledger['name']}"
        )

    if fallback_gebruikt:
        print(
            "   ! Let op: cyflux heeft geen BTW-tarief voor het gevraagde "
            "percentage. Brutobedrag (incl BTW) is geboekt onder "
            "'Btw vrijgesteld'. Voeg in Moneybird tarieven toe voor "
            "echte BTW-splitsing."
        )

    print(">> Stap 4: Inkoopfactuur aanmaken in Moneybird...")
    if prices_incl_tax_override is not None:
        prijzen_incl = prices_incl_tax_override
    else:
        prijzen_incl = bool(data.get("prijzen_incl_btw", False))
    payload = {
        "contact_id": contact["id"],
        "reference": data["factuurnummer"],
        "date": data["datum"],
        "due_date": data.get("vervaldatum"),
        "currency": data.get("valuta", "EUR"),
        "prices_are_incl_tax": prijzen_incl,
        "details_attributes": details,
    }
    payload = {k: v for k, v in payload.items() if v is not None}
    factuur = mb.create_purchase_invoice(payload)
    totaal = factuur.get("total_price_incl_tax")
    print(f"   factuur_id={factuur['id']} totaal=EUR {totaal}")

    print(">> Stap 5: PDF als bijlage uploaden...")
    pdf_bijlage_ok = False
    pdf_bijlage_fout = None
    try:
        mb.attach_pdf_purchase(factuur["id"], pdf_path)
        pdf_bijlage_ok = True
        print("   PDF gekoppeld als bijlage.")
    except Exception as e:
        pdf_bijlage_fout = str(e)
        print(f"   !! PDF upload mislukt: {e}")

    # Totalen zelf berekenen: Rompslomp geeft in de expense-respons geen
    # total_price_incl_tax terug, alleen de regels. We rekenen op basis van de
    # geboekte BTW-percentages en de incl/excl-vlag.
    def _pct(r):
        try:
            return float(r.get("btw_pct_geboekt") or 0)
        except (TypeError, ValueError):
            return 0.0
    regel_sum = sum(r["aantal"] * r["prijs_per_stuk"] for r in trace_regels)
    if prijzen_incl:
        totaal_incl = regel_sum
        totaal_excl = sum(
            r["aantal"] * r["prijs_per_stuk"] / (1 + _pct(r) / 100)
            for r in trace_regels
        )
    else:
        totaal_excl = regel_sum
        totaal_incl = sum(
            r["aantal"] * r["prijs_per_stuk"] * (1 + _pct(r) / 100)
            for r in trace_regels
        )

    # Beslissings-trace voor de sandbox/review (raakt bestaande callers niet:
    # die negeren onbekende keys op het factuur-object).
    if isinstance(factuur, dict):
        factuur["_boeking_trace"] = {
            "contact": {
                "id": contact.get("id"),
                "naam": (
                    contact.get("company_name")
                    or contact.get("contact_person_name")
                    or contact.get("firstname")
                ),
                "nieuw_aangemaakt": bool(contact.get("_nieuw_aangemaakt")),
            },
            "regels": trace_regels,
            "prijzen_incl_btw": prijzen_incl,
            "fallback_btw_gebruikt": fallback_gebruikt,
            "min_zekerheid": (
                round(min((tr.get("zekerheid", 0.0) for tr in trace_regels), default=0.0), 2)
            ),
            "needs_review": any(
                tr.get("zekerheid", 0.0) < DREMPEL for tr in trace_regels
            ),
            "totaal_excl_btw": round(totaal_excl, 2),
            "totaal_incl_btw": (
                factuur.get("total_price_incl_tax")
                if factuur.get("total_price_incl_tax") is not None
                else round(totaal_incl, 2)
            ),
            "pdf_bijlage_ok": pdf_bijlage_ok,
            "pdf_bijlage_fout": pdf_bijlage_fout,
        }

    return factuur


def vind_of_maak_klant(mb, klant):
    """Voor verkoopfacturen: contact als KLANT (debiteur) aanmaken."""
    naam = klant.get("company_name") or klant.get("firstname") or ""
    bestaand = mb.find_contact(naam) if naam else None
    if bestaand:
        return bestaand

    payload = {
        "company_name": klant.get("company_name"),
        "firstname": klant.get("firstname"),
        "lastname": klant.get("lastname"),
        "address1": klant.get("address1"),
        "address2": klant.get("address2"),
        "zipcode": klant.get("zipcode"),
        "city": klant.get("city"),
        "country": klant.get("country", "NL"),
        "phone": klant.get("phone"),
        "email": klant.get("email"),
        "tax_number": klant.get("tax_number"),
        "chamber_of_commerce": klant.get("chamber_of_commerce"),
        "customer": True,
    }
    if klant.get("iban"):
        payload["sepa_iban"] = klant["iban"]
        if klant.get("company_name"):
            payload["sepa_iban_account_name"] = klant["company_name"]

    payload = {k: v for k, v in payload.items() if v not in (None, "")}
    return mb.create_contact(payload)


def kies_grootboek_verkoop(ledgers, hint):
    """Kies een verkoop-grootboek (omzet). Voorkeur: 'Omzet' / 'Verkoop'."""
    if not ledgers:
        raise RuntimeError(
            "Geen verkoop-grootboekrekeningen beschikbaar in dit boekhoudpakket. "
            "Controleer of de administratie omzet-grootboeken heeft."
        )
    if hint:
        h = hint.lower().strip()
        for l in ledgers:
            naam = (l.get("name") or "").lower()
            if naam and (h in naam or naam in h):
                return l

    for naam in ("omzet hoog", "omzet 21", "omzet", "verkoop"):
        for l in ledgers:
            if naam in (l.get("name") or "").lower():
                return l
    revenue = [l for l in ledgers if l.get("account_type") == "revenue"]
    if revenue:
        return revenue[0]
    return ledgers[0]


def boek_verkoop(data, pdf_path, mark_open=True):
    """
    Boek 1 verkoopfactuur in Moneybird.

    data: zelfde structuur als boek(), maar 'leverancier' is hier de KLANT
    (degene aan wie wij factureerden) - we hergebruiken het veld voor
    parser-compatibiliteit, maar in Moneybird wordt het een 'customer' contact.
    mark_open: zet status van 'draft' op 'open' (definitief / te ontvangen)
    """
    mb = get_provider()

    print(">> [Verkoop] Stap 1: Klant ophalen of aanmaken...")
    klant_data = data.get("klant") or data.get("leverancier") or {}
    contact = vind_of_maak_klant(mb, klant_data)
    print(f"   contact_id={contact['id']} ({contact.get('company_name')})")

    print(">> [Verkoop] Stap 2: Verkoop-grootboeken ophalen...")
    ledgers = mb.sales_ledgers()
    print(f"   {len(ledgers)} bruikbare verkoop-grootboekrekeningen")

    print(">> [Verkoop] Stap 3: Factuurregels samenstellen...")
    details = []
    fallback_gebruikt = False
    prices_incl_tax_override = None
    for r in data["regels"]:
        ledger = kies_grootboek_verkoop(ledgers, r.get("grootboek_hint"))
        gevraagd_pct = r.get("btw_percentage")
        tax = mb.find_or_create_tax_rate(
            gevraagd_pct, tax_rate_type="sales_invoice"
        )
        is_vrijgesteld = tax.get("percentage") is None
        if gevraagd_pct not in (None, 0) and is_vrijgesteld:
            fallback_gebruikt = True

        aantal = int(r.get("aantal", 1))
        if gevraagd_pct not in (None, 0) and is_vrijgesteld:
            prijs = float(r["prijs_per_stuk"]) * (1 + float(gevraagd_pct) / 100)
            prices_incl_tax_override = True
        else:
            prijs = float(r["prijs_per_stuk"])

        details.append({
            "description": r["omschrijving"],
            "amount": f"{aantal} x",
            "price": f"{prijs:.2f}",
            "tax_rate_id": tax["id"],
            "ledger_account_id": ledger["id"] if ledger else None,
        })
        details[-1] = {k: v for k, v in details[-1].items() if v is not None}
        print(
            f"   regel: {r['omschrijving'][:40]:40s} | "
            f"EUR {prijs:.2f} x {aantal} | "
            f"BTW {gevraagd_pct}% -> {tax['name']} | "
            f"grootboek={ledger['name'] if ledger else '-'}"
        )

    print(">> [Verkoop] Stap 4: Verkoopfactuur aanmaken in Moneybird...")
    if prices_incl_tax_override is not None:
        prijzen_incl = prices_incl_tax_override
    else:
        prijzen_incl = bool(data.get("prijzen_incl_btw", False))
    payload = {
        "contact_id": contact["id"],
        "reference": data.get("factuurnummer"),
        "invoice_date": data["datum"],
        "due_date": data.get("vervaldatum"),
        "currency": data.get("valuta", "EUR"),
        "prices_are_incl_tax": prijzen_incl,
        "details_attributes": details,
    }
    payload = {k: v for k, v in payload.items() if v is not None}
    factuur = mb.create_sales_invoice(payload)
    totaal = factuur.get("total_price_incl_tax")
    print(f"   factuur_id={factuur['id']} totaal=EUR {totaal} state={factuur.get('state')}")

    if mark_open and factuur.get("state") in (None, "draft"):
        print(">> [Verkoop] Stap 5: Factuur op 'open' zetten (definitief)...")
        try:
            mb.send_sales_invoice(factuur["id"], delivery_method="Manual")
            # opnieuw ophalen voor up-to-date state
            factuur["state"] = "open"
            print("   Status: open")
        except Exception as e:
            print(f"   !! Status-update mislukt: {e}")

    print(">> [Verkoop] Stap 6: PDF als bijlage uploaden...")
    try:
        mb.attach_pdf_sales(factuur["id"], pdf_path)
        print("   PDF gekoppeld als bijlage.")
    except Exception as e:
        print(f"   !! PDF upload mislukt: {e}")

    return factuur


def main():
    if len(sys.argv) != 3:
        print("gebruik: python boek_agent.py factuur_data.json /pad/naar/factuur.pdf")
        return 2

    data_path = Path(sys.argv[1])
    pdf_path = Path(sys.argv[2])

    data = json.loads(data_path.read_text(encoding="utf-8"))
    factuur = boek(data, pdf_path)

    print()
    print("=" * 60)
    print("KLAAR - factuur is geboekt in Moneybird.")
    print("=" * 60)
    print(f"id:        {factuur['id']}")
    print(f"referentie:{factuur.get('reference')}")
    print(f"datum:     {factuur.get('date')}")
    totaal = factuur.get("total_price_incl_tax")
    print(f"totaal:    EUR {totaal}")
    print(f"status:    {factuur.get('state')}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
