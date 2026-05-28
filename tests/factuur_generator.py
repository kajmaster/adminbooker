"""
factuur_generator.py - genereert synthetische inkoopfacturen + ground truth JSON.

Genereert PDFs met variatie in:
- BTW-tarief (21%, 9%, 0%/verlegd, vrijgesteld)
- Taal (NL, EN)
- Aantal regels (1, meerdere, mix tarieven)
- Leveranciersgegevens (volledig vs minimaal)
- Layout-varianten (klassiek, bonnetje, ZZP)

Output:
  tests/dataset/factuur_NN.pdf
  tests/dataset/factuur_NN.truth.json
"""
from __future__ import annotations

import json
import os
import random
from datetime import date, timedelta
from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import mm
from reportlab.platypus import (
    SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, PageBreak
)


HERE = Path(__file__).resolve().parent
DATASET = HERE / "dataset"
DATASET.mkdir(parents=True, exist_ok=True)


# ---------- helpers ----------

def euro(x):
    return f"€ {x:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")


def render_factuur(pdf_path, data, taal="nl"):
    """
    Render een PDF op basis van data dict (leverancier, regels etc).
    Eenvoudige klassieke layout.
    """
    L = {
        "nl": {
            "factuur": "FACTUUR",
            "factuurnummer": "Factuurnummer",
            "datum": "Factuurdatum",
            "vervaldatum": "Vervaldatum",
            "klant": "Klant",
            "omschrijving": "Omschrijving",
            "aantal": "Aantal",
            "prijs": "Prijs",
            "totaal_regel": "Totaal",
            "subtotaal": "Subtotaal",
            "btw": "BTW",
            "totaal": "Totaal",
            "btw_verlegd": "BTW verlegd",
            "kvk": "KvK",
            "btw_nr": "BTW",
            "iban": "IBAN",
            "termen": "Te betalen binnen {} dagen.",
        },
        "en": {
            "factuur": "INVOICE",
            "factuurnummer": "Invoice #",
            "datum": "Date",
            "vervaldatum": "Due Date",
            "klant": "Bill to",
            "omschrijving": "Description",
            "aantal": "Qty",
            "prijs": "Price",
            "totaal_regel": "Amount",
            "subtotaal": "Subtotal",
            "btw": "VAT",
            "totaal": "Total",
            "btw_verlegd": "VAT reverse charged",
            "kvk": "CoC",
            "btw_nr": "VAT",
            "iban": "IBAN",
            "termen": "Payment due within {} days.",
        },
    }[taal]

    doc = SimpleDocTemplate(
        str(pdf_path), pagesize=A4,
        leftMargin=20*mm, rightMargin=20*mm, topMargin=20*mm, bottomMargin=20*mm
    )
    styles = getSampleStyleSheet()
    h_title = ParagraphStyle("title", parent=styles["Heading1"], fontSize=24, alignment=2)
    h_lev = ParagraphStyle("lev", parent=styles["Normal"], fontSize=11, leading=14)
    p_meta = ParagraphStyle("meta", parent=styles["Normal"], fontSize=10, leading=13)

    story = []
    lev = data["leverancier"]

    # Header: leverancier links, FACTUUR titel rechts
    lev_html = f"<b>{lev.get('company_name', '')}</b><br/>"
    if lev.get("address1"):
        lev_html += f"{lev['address1']}<br/>"
    if lev.get("zipcode") or lev.get("city"):
        lev_html += f"{lev.get('zipcode', '')} {lev.get('city', '')}<br/>"
    if lev.get("country"):
        lev_html += f"{lev['country']}<br/>"
    parts = []
    if lev.get("chamber_of_commerce"):
        parts.append(f"{L['kvk']}: {lev['chamber_of_commerce']}")
    if lev.get("tax_number"):
        parts.append(f"{L['btw_nr']}: {lev['tax_number']}")
    if lev.get("iban"):
        parts.append(f"{L['iban']}: {lev['iban']}")
    if parts:
        lev_html += "<br/>".join(parts)

    header_tbl = Table(
        [[Paragraph(lev_html, h_lev), Paragraph(f"<b>{L['factuur']}</b><br/># {data['factuurnummer']}", h_title)]],
        colWidths=[100*mm, 70*mm]
    )
    header_tbl.setStyle(TableStyle([("VALIGN", (0,0), (-1,-1), "TOP")]))
    story.append(header_tbl)
    story.append(Spacer(1, 10*mm))

    # Meta-blok rechts: klant + datums
    meta_html = (
        f"<b>{L['klant']}:</b><br/>{data.get('klant', 'Cyflux')}<br/><br/>"
        f"<b>{L['datum']}:</b> {data['datum']}<br/>"
        f"<b>{L['vervaldatum']}:</b> {data['vervaldatum']}"
    )
    story.append(Paragraph(meta_html, p_meta))
    story.append(Spacer(1, 8*mm))

    # Regels-tabel
    rows = [[L["omschrijving"], L["aantal"], L["prijs"], L["totaal_regel"]]]
    for r in data["regels"]:
        rows.append([
            r["omschrijving"],
            str(r["aantal"]),
            euro(r["prijs_per_stuk"]),
            euro(r["aantal"] * r["prijs_per_stuk"]),
        ])
    tbl = Table(rows, colWidths=[80*mm, 20*mm, 30*mm, 30*mm])
    tbl.setStyle(TableStyle([
        ("BACKGROUND", (0,0), (-1,0), colors.HexColor("#333333")),
        ("TEXTCOLOR", (0,0), (-1,0), colors.white),
        ("ALIGN", (1,0), (-1,-1), "RIGHT"),
        ("ALIGN", (0,0), (0,-1), "LEFT"),
        ("FONTNAME", (0,0), (-1,0), "Helvetica-Bold"),
        ("BOTTOMPADDING", (0,0), (-1,0), 6),
        ("TOPPADDING", (0,0), (-1,0), 6),
        ("LINEBELOW", (0,0), (-1,0), 0.5, colors.black),
        ("LINEABOVE", (0,1), (-1,-1), 0.25, colors.HexColor("#cccccc")),
    ]))
    story.append(tbl)
    story.append(Spacer(1, 6*mm))

    # Totalen
    sub = sum(r["aantal"] * r["prijs_per_stuk"] for r in data["regels"])
    btw_label = L["btw"]
    if data.get("btw_verlegd"):
        btw_text = L["btw_verlegd"]
        btw_bedrag = 0.0
    elif data["regels"][0].get("btw_percentage", 0) == 0:
        btw_text = f"{btw_label} (0%)"
        btw_bedrag = 0.0
    else:
        # mix mogelijk: per regel
        btw_bedrag = sum(
            r["aantal"] * r["prijs_per_stuk"] * ((r.get("btw_percentage") or 0) / 100)
            for r in data["regels"]
        )
        # toon enkelvoudig als alle regels zelfde tarief
        tarieven = {(r.get("btw_percentage") or 0) for r in data["regels"]}
        if len(tarieven) == 1:
            btw_text = f"{btw_label} ({list(tarieven)[0]}%)"
        else:
            btw_text = f"{btw_label} (mix)"
    total = sub + btw_bedrag

    tot_rows = [
        [L["subtotaal"], euro(sub)],
        [btw_text, euro(btw_bedrag)],
        [L["totaal"], euro(total)],
    ]
    tot_tbl = Table(tot_rows, colWidths=[140*mm, 30*mm])
    tot_tbl.setStyle(TableStyle([
        ("ALIGN", (0,0), (-1,-1), "RIGHT"),
        ("FONTNAME", (0,2), (-1,2), "Helvetica-Bold"),
        ("LINEABOVE", (0,2), (-1,2), 0.5, colors.black),
        ("TOPPADDING", (0,0), (-1,-1), 4),
    ]))
    story.append(tot_tbl)

    # Voorwaarden
    if data.get("betaaltermijn_dagen"):
        story.append(Spacer(1, 8*mm))
        story.append(Paragraph(L["termen"].format(data["betaaltermijn_dagen"]), p_meta))

    doc.build(story)


def base_truth(idx, **overrides):
    """Genereer een ground-truth dict, dan toepassen op render_factuur."""
    today = date(2026, 4, 1) - timedelta(days=idx*7)
    due = today + timedelta(days=14)
    truth = {
        "factuurnummer": f"INV-2026-{idx:03d}",
        "datum": today.isoformat(),
        "vervaldatum": due.isoformat(),
        "valuta": "EUR",
        "betaaltermijn_dagen": 14,
        "leverancier": {
            "company_name": "Voorbeeld B.V.",
            "address1": "Hoofdstraat 1",
            "zipcode": "1234 AB",
            "city": "Amsterdam",
            "country": "NL",
            "chamber_of_commerce": "12345678",
            "tax_number": "NL123456789B01",
            "iban": "NL00BANK0123456789",
        },
        "klant": "Cyflux",
        "regels": [
            {"omschrijving": "Dienst",
             "aantal": 1, "prijs_per_stuk": 100.00, "btw_percentage": 21}
        ],
    }
    truth.update(overrides)
    if "leverancier" in overrides:
        # Merge in plaats van overwrite, om defaults te behouden
        merged = dict(truth["leverancier"])
        merged.update(overrides["leverancier"])
        truth["leverancier"] = merged
    return truth


# ---------- 10 verschillende facturen ----------

CASES = []

# 1. Klassieke NL factuur, 21% BTW, volledige leverancier
CASES.append(("nl", base_truth(1, leverancier={
    "company_name": "Hosting Solutions B.V.",
    "address1": "Kerkstraat 12", "zipcode": "3511 AB", "city": "Utrecht",
    "country": "NL", "chamber_of_commerce": "30123456",
    "tax_number": "NL821234567B01", "iban": "NL91ABNA0417164300",
}, regels=[
    {"omschrijving": "Webhosting Q2 2026", "aantal": 1,
     "prijs_per_stuk": 49.95, "btw_percentage": 21},
])))

# 2. Laag BTW-tarief 9% (horeca/voeding)
CASES.append(("nl", base_truth(2, leverancier={
    "company_name": "De Bakkerij Vermeer",
    "address1": "Marktplein 8", "zipcode": "5611 EM", "city": "Eindhoven",
    "country": "NL", "chamber_of_commerce": "17098765",
    "tax_number": "NL856789012B02", "iban": "NL44RABO0123456789",
}, regels=[
    {"omschrijving": "Lunch vergadering 14 personen", "aantal": 14,
     "prijs_per_stuk": 8.50, "btw_percentage": 9},
])))

# 3. BTW verlegd (0%, intra-EU of subcontractor bouw)
truth3 = base_truth(3, leverancier={
    "company_name": "Stuc & Plafond Werken VOF",
    "address1": "Industrieweg 22", "zipcode": "5048 AB", "city": "Tilburg",
    "country": "NL", "chamber_of_commerce": "45678901",
    "tax_number": "NL812345678B01", "iban": "NL18INGB0123456789",
}, regels=[
    {"omschrijving": "Stucwerk woonkamer 45m2", "aantal": 1,
     "prijs_per_stuk": 1850.00, "btw_percentage": 0},
])
truth3["btw_verlegd"] = True
CASES.append(("nl", truth3))

# 4. Engelstalige factuur, 21% (zoals Demo eerder)
CASES.append(("en", base_truth(4, leverancier={
    "company_name": "ACME Software Inc.",
    "address1": "1 Market Street", "zipcode": "94105", "city": "San Francisco",
    "country": "US",
    "tax_number": "EU826000000",
}, regels=[
    {"omschrijving": "SaaS subscription Pro - April 2026", "aantal": 1,
     "prijs_per_stuk": 79.00, "btw_percentage": 21},
])))

# 5. Meerdere regels met mix BTW-tarieven (kantoorartikelen en lunch)
CASES.append(("nl", base_truth(5, leverancier={
    "company_name": "Office Plaza Nederland",
    "address1": "Logistiekweg 5", "zipcode": "3771 ME", "city": "Barneveld",
    "country": "NL", "chamber_of_commerce": "20345678",
    "tax_number": "NL801234567B01", "iban": "NL12RABO0987654321",
}, regels=[
    {"omschrijving": "A4 papier 5x500 vel", "aantal": 5,
     "prijs_per_stuk": 4.99, "btw_percentage": 21},
    {"omschrijving": "Pennen blauw doos 50", "aantal": 2,
     "prijs_per_stuk": 12.95, "btw_percentage": 21},
    {"omschrijving": "Koffie en thee bedrijfskeuken", "aantal": 1,
     "prijs_per_stuk": 38.50, "btw_percentage": 9},
])))

# 6. Bonnetje-stijl, korte info
CASES.append(("nl", base_truth(6, leverancier={
    "company_name": "Tankstation Shell A12",
    "address1": "A12 Highway km 22", "zipcode": "3540 AA", "city": "Utrecht",
    "country": "NL",
}, regels=[
    {"omschrijving": "Brandstof Euro95", "aantal": 1,
     "prijs_per_stuk": 67.40, "btw_percentage": 21},
])))

# 7. Meerdere regels enkel tarief (telecom)
CASES.append(("nl", base_truth(7, leverancier={
    "company_name": "Telecombedrijf NL",
    "address1": "Telekomplein 100", "zipcode": "3013 AA", "city": "Rotterdam",
    "country": "NL", "chamber_of_commerce": "27123456",
    "tax_number": "NL811111111B01", "iban": "NL56INGB0987654321",
}, regels=[
    {"omschrijving": "Mobiel abonnement bedrijf 5 lijnen",
     "aantal": 5, "prijs_per_stuk": 32.00, "btw_percentage": 21},
    {"omschrijving": "Internet glasvezel zakelijk",
     "aantal": 1, "prijs_per_stuk": 79.50, "btw_percentage": 21},
])))

# 8. Vrijgesteld (geen BTW, bv. KOR-leverancier of opleiding)
CASES.append(("nl", base_truth(8, leverancier={
    "company_name": "Coach & Training Anna de Vries",
    "address1": "Berkenlaan 3", "zipcode": "3702 ER", "city": "Zeist",
    "country": "NL", "chamber_of_commerce": "55556666",
}, regels=[
    {"omschrijving": "Coachingssessie 1.5u", "aantal": 1,
     "prijs_per_stuk": 175.00, "btw_percentage": None},
])))

# 9. Internationale leverancier EU (BE)
CASES.append(("nl", base_truth(9, leverancier={
    "company_name": "Belgisch Drukwerk BVBA",
    "address1": "Antwerpsesteenweg 250", "zipcode": "2640", "city": "Mortsel",
    "country": "BE", "tax_number": "BE0123456789",
    "iban": "BE68539007547034",
}, regels=[
    {"omschrijving": "Brochures full color 1000 stuks",
     "aantal": 1, "prijs_per_stuk": 425.00, "btw_percentage": 0},
])))

# 10. Hoge waarde, langere omschrijving, twee regels 21%
CASES.append(("nl", base_truth(10, leverancier={
    "company_name": "IT Consultancy van der Berg",
    "address1": "Computerweg 14", "zipcode": "3542 DR", "city": "Utrecht",
    "country": "NL", "chamber_of_commerce": "31987654",
    "tax_number": "NL856654321B01", "iban": "NL21INGB0001234567",
}, regels=[
    {"omschrijving": "Implementatie ERP-koppeling fase 1",
     "aantal": 40, "prijs_per_stuk": 125.00, "btw_percentage": 21},
    {"omschrijving": "Reiskosten op locatie",
     "aantal": 1, "prijs_per_stuk": 187.50, "btw_percentage": 21},
])))


def main():
    for i, (taal, truth) in enumerate(CASES, start=1):
        pdf = DATASET / f"factuur_{i:02d}.pdf"
        truth_path = DATASET / f"factuur_{i:02d}.truth.json"
        render_factuur(pdf, truth, taal=taal)
        truth_path.write_text(
            json.dumps(truth, indent=2, ensure_ascii=False),
            encoding="utf-8"
        )
        print(f"  {pdf.name} ({taal}, {len(truth['regels'])} regels)")
    print(f"\n{len(CASES)} facturen + ground truth in {DATASET}")


if __name__ == "__main__":
    main()
