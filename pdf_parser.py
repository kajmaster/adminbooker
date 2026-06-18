"""
pdf_parser.py - extraheer factuurgegevens uit een inkoopfactuur-PDF.

Robuuste versie: probeert eerst expliciete velden (Factuurdatum: ..., BTW: ..., etc.)
en valt daarna terug op heuristieken (alle datums op de pagina, alle euro-bedragen,
neem grootste als totaal). Als regels niet kunnen worden ontleed, wordt 1 synthetische
regel opgebouwd uit het totaal zodat de factuur in elk geval geboekt kan worden.

Output: dict met dezelfde structuur als boek_agent.py verwacht.
"""
from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path

import pdfplumber


def _clean_amount(s):
    """Parse Nederlandse en Engelse bedragnotatie naar float."""
    if s is None:
        return 0.0
    s = str(s).strip().replace("€", "").replace("EUR", "").strip()
    m = re.search(r"[\d\.\,]+", s)
    if not m:
        return 0.0
    num = m.group(0)
    if num.count(",") == 1 and re.search(r",\d{2}$", num):
        num = num.replace(".", "").replace(",", ".")
    elif num.count(".") == 1 and num.count(",") >= 1:
        num = num.replace(",", "")
    elif num.count(",") == 1 and num.count(".") == 0:
        num = num.replace(",", ".")
    try:
        return float(num)
    except ValueError:
        return 0.0


MONTHS = {
    "jan":1,"feb":2,"mar":3,"apr":4,"may":5,"jun":6,"jul":7,"aug":8,"sep":9,"oct":10,"nov":11,"dec":12,
    "january":1,"february":2,"march":3,"april":4,"june":6,"july":7,"august":8,"september":9,"october":10,"november":11,"december":12,
    "januari":1,"februari":2,"maart":3,"mei":5,"juni":6,"juli":7,"augustus":8,"september":9,"oktober":10,"november":11,"december":12,
    "mrt":3,"mei":5,"okt":10,
}


def _parse_date(s):
    """Parse meerdere datumnotaties naar ISO YYYY-MM-DD, of None."""
    if not s:
        return None
    s = s.strip()
    # YYYY-MM-DD
    m = re.match(r"^(\d{4})-(\d{1,2})-(\d{1,2})$", s)
    if m:
        try:
            return datetime(int(m.group(1)), int(m.group(2)), int(m.group(3))).date().isoformat()
        except ValueError:
            return None
    # DD-MM-YYYY of DD/MM/YYYY
    m = re.match(r"^(\d{1,2})[-/](\d{1,2})[-/](\d{4})$", s)
    if m:
        try:
            return datetime(int(m.group(3)), int(m.group(2)), int(m.group(1))).date().isoformat()
        except ValueError:
            return None
    # "18 dec 2025" / "December 18, 2025"
    m = re.match(r"^(\d{1,2})\s+([A-Za-z\.]+)\s+(\d{4})$", s)
    if m:
        mon = MONTHS.get(m.group(2).lower().rstrip(".")[:3]) or MONTHS.get(m.group(2).lower().rstrip("."))
        if mon:
            try:
                return datetime(int(m.group(3)), mon, int(m.group(1))).date().isoformat()
            except ValueError:
                return None
    m = re.match(r"^([A-Za-z\.]+)\s+(\d{1,2}),?\s+(\d{4})$", s)
    if m:
        mon = MONTHS.get(m.group(1).lower().rstrip(".")[:3]) or MONTHS.get(m.group(1).lower().rstrip("."))
        if mon:
            try:
                return datetime(int(m.group(3)), mon, int(m.group(2))).date().isoformat()
            except ValueError:
                return None
    return None


# Patronen om alle datums op de pagina te vinden
ALL_DATES_RE = re.compile(
    r"\b("
    r"\d{4}-\d{1,2}-\d{1,2}"
    r"|\d{1,2}[-/]\d{1,2}[-/]\d{4}"
    r"|\d{1,2}\s+[A-Za-z\.]+\s+\d{4}"
    r"|[A-Za-z\.]+\s+\d{1,2},?\s+\d{4}"
    r")\b"
)

# Alle bedragen, eventueel met EUR/€ ervoor
ALL_AMOUNTS_RE = re.compile(
    r"(?:€|EUR)?\s*(\d{1,3}(?:[.,]\d{3})*[.,]\d{2})"
)

KVK_RE = re.compile(r"(?:KvK|CoC|KVK|kvk)[\s:.]*?([0-9]{8})")
BTW_RE = re.compile(r"(?:BTW|VAT|btw|tax)[\s\.:#]*?([A-Z]{2}[A-Z0-9\.\-]{8,15})", re.IGNORECASE)
IBAN_RE = re.compile(r"IBAN[\s:.]*([A-Z]{2}[0-9]{2}[A-Z0-9]{4}[0-9]{7,16})", re.IGNORECASE)

INVOICE_NR_RE_LIST = [
    re.compile(r"(?:Factuurnummer|Invoice\s*number|Invoice\s*No\.?|Invoice\s*#)\s*[:#]?\s*([A-Z0-9][A-Z0-9\-/_\.]{1,30})", re.IGNORECASE),
    re.compile(r"(?:Nr\.?|Nummer|Reference)\s*[:#]?\s*([A-Z0-9][A-Z0-9\-/_\.]{2,30})", re.IGNORECASE),
    re.compile(r"#\s*([A-Z0-9][A-Z0-9\-/_\.]{2,30})"),
]

DATE_LABELED = [
    (re.compile(r"(?:Factuurdatum|Invoice\s*Date)\s*[:#]?\s*([^\n]+?)(?:\n|$)", re.IGNORECASE), "datum"),
    (re.compile(r"\bDate\s*[:#]\s*([^\n]+?)(?:\n|$)", re.IGNORECASE), "datum"),
    (re.compile(r"(?:Vervaldatum|Due\s*Date|Payment\s*Due)\s*[:#]?\s*([^\n]+?)(?:\n|$)", re.IGNORECASE), "vervaldatum"),
]

TOTAAL_LABELED = [
    re.compile(r"(?:Totaal\s*incl|Total\s*incl|Te\s*betalen|Balance\s*Due|Amount\s*Due|Total)\s*[:#]?\s*€?\s*([\d\.\,]+)", re.IGNORECASE),
    re.compile(r"^Totaal\s+€?\s*([\d\.\,]+)\s*$", re.IGNORECASE | re.MULTILINE),
]
SUBTOTAAL_LABELED = re.compile(r"(?:Subtotaal|Subtotal|Totaal\s*excl)\s*[:#]?\s*€?\s*([\d\.\,]+)", re.IGNORECASE)
BTW_LABELED = re.compile(
    r"(?:BTW|VAT|Tax)\s*\(?(\d{1,2})\s*%\)?\s*[:#]?\s*(?:€|EUR\.?)?\s*([\d\.\,]+)", re.IGNORECASE
)
# Fallback: 'BTW 21%' / 'BTW: 21%' / 'VAT 9%' zonder bedrag in meta-blok
BTW_META_RE = re.compile(
    r"\b(?:BTW|VAT)\b\s*[:#]?\s*(\d{1,2})\s*%(?!\s*\))",
    re.IGNORECASE,
)
BTW_VERLEGD_RE = re.compile(r"BTW\s*verlegd|VAT\s*reverse\s*charged|reverse\s*charge", re.IGNORECASE)


# Anker-woorden voor een twee-koloms kop: leverancier/verkoper staat links,
# klant/afnemer rechts (NL + EN). Voor 'Bill To' is het anker 'bill'.
_KOLOM_LINKS_RE = re.compile(
    r"^(from|verkoper|afzender|leverancier|seller|sender|vendor)$", re.IGNORECASE
)
_KOLOM_RECHTS_RE = re.compile(
    r"^(bill|ship|sold|klant|afnemer|koper|customer)$", re.IGNORECASE
)


def _leverancier_uit_kolommen(words):
    """Ontwar een twee-koloms kop (leverancier links, klant rechts) via
    woord-posities.

    Herkent zowel Engelse koppen ('From ... Bill To') als Nederlandse
    ('Verkoper ... Klant', 'Afzender ... Afnemer'). In zulke layouts lopen de
    leverancier (linkerkolom) en de klant (rechterkolom) in de platte tekst
    door elkaar, en plakken losse watermerk-letters ('S','A','M','P','L','E'
    of 'V','O','O','R','B','E','E','L','D') ertussen. We pakken hier de
    linkerkolom op x-positie en geven (company_name, [adresregels]) terug.

    Retourneert (None, []) als er geen herkenbare twee-koloms-kop is.
    """
    if not words:
        return None, []
    # Zoek een anker-paar op dezelfde kopregel: een links-anker (leverancier)
    # met een rechts-anker (klant) errechts van. Dat dubbele, naast-elkaar-
    # staande signaal kenmerkt een twee-koloms-kop en voorkomt valse matches
    # met losse woorden elders op de pagina.
    from_w = bill_w = None
    for lw in words:
        if not _KOLOM_LINKS_RE.match((lw.get("text") or "").strip()):
            continue
        for rw in words:
            if (_KOLOM_RECHTS_RE.match((rw.get("text") or "").strip())
                    and rw["x0"] > lw["x1"]
                    and abs(rw["top"] - lw["top"]) <= 4):
                from_w, bill_w = lw, rw
                break
        if from_w:
            break
    if not from_w or not bill_w:
        return None, []

    grens = (from_w["x1"] + bill_w["x0"]) / 2.0
    header_bottom = max(from_w["bottom"], bill_w["bottom"])

    # Linkerkolom: woorden onder de koppen, links van de grens, geen losse letters.
    left = [
        w for w in words
        if w["top"] >= header_bottom - 1
        and w["x1"] <= grens
        and len((w.get("text") or "").strip()) > 1
    ]
    if not left:
        return None, []

    left.sort(key=lambda w: (round(w["top"], 1), w["x0"]))
    regels, huidige, huidige_top = [], [], None
    for w in left:
        if huidige_top is None or abs(w["top"] - huidige_top) <= 3:
            huidige.append(w)
            if huidige_top is None:
                huidige_top = w["top"]
        else:
            regels.append(" ".join(x["text"] for x in huidige).strip())
            huidige, huidige_top = [w], w["top"]
    if huidige:
        regels.append(" ".join(x["text"] for x in huidige).strip())
    regels = [r for r in regels if r]
    if not regels:
        return None, []
    return regels[0][:100], regels[1:]


# Labels die het LEVERANCIER-blok aankondigen (NL + EN). De naam staat op
# dezelfde regel achter de dubbele punt, of op de regels eronder. Dubbele punt
# is verplicht zodat 'Van Dijk Bouwgroep' niet als label 'Van:' wordt gezien.
SUPPLIER_LABEL_RE = re.compile(
    r"^\s*(?:leverancier|afzender|verkoper|supplier|sender|seller|vendor|van)"
    r"\b(?:\s*/\s*\w+)?\s*:\s*(.*)$",
    re.IGNORECASE,
)
# Labels die het AFNEMER/klant-blok aankondigen: einde van het leveranciersblok.
CUSTOMER_LABEL_RE = re.compile(
    r"^\s*(?:afnemer|klant|koper|debiteur|customer|bill\s*to|ship\s*to|"
    r"sold\s*to|factuuradres|aan|to)\b\s*:?",
    re.IGNORECASE,
)
# Meta-regels (factuurnummer/datum/…) markeren ook het einde van het blok.
_META_LABEL_RE = re.compile(
    r"^\s*(?:factuurnummer|factuurdatum|vervaldatum|invoice|date|referentie|"
    r"reference|betalings?termijn|betalingskenmerk|order)\b",
    re.IGNORECASE,
)
# Sample-/watermerk-/disclaimerteksten: dit is NOOIT een leveranciersnaam.
WATERMARK_RE = re.compile(
    r"\bsample\b|\bspecimen\b|geen\s+echte|niet[\s-]*geldig|demonstratie|"
    r"voorbeeld\s*factuur|test[\s-]*factuur|uitsluitend\s+voor\s+(?:test|demo)|"
    r"\bproforma\b|pro\s*forma",
    re.IGNORECASE,
)
# Doc-type-woorden die nooit een contactnaam zijn. De samengestelde vormen
# (VERKOOPFACTUUR/INKOOPFACTUUR/CREDITFACTUUR/CREDITNOTA) staan vooraan zodat de
# alternation ze als geheel pakt — anders matcht \bFACTUUR\b er niet in.
_DOC_MARKER = (
    r"(VERKOOPFACTUUR|INKOOPFACTUUR|CREDITFACTUUR|CREDITNOTA|"
    r"FACTUUR|INVOICE|FACTURE|RECHNUNG|NOTA|BILL|RECEIPT|KWITANTIE)"
)


def _schoon_leveranciernaam(naam):
    """Strip doc-markers ('FACTUUR'/'INVOICE'/…) van een kandidaat-naam af."""
    naam = naam.strip()[:100]
    naam = re.sub(r"\s*\b" + _DOC_MARKER + r"\b.*$", "", naam,
                  flags=re.IGNORECASE).strip()
    naam = re.sub(r"^" + _DOC_MARKER + r"\s*[#:\-]?\s*", "", naam,
                  flags=re.IGNORECASE).strip()
    return naam


def _leverancier_uit_label(lines):
    """Zoek het leveranciersblok via een expliciet label ('Leverancier:',
    'Afzender:', 'Supplier:', …) en geef (company_name, [adresregels]) terug.

    Dit is het sterkste signaal op NL-facturen: het negeert een sample-/
    watermerkregel bovenaan ('SAMPLE - GEEN ECHTE FACTUUR') én het afnemer-blok,
    en pakt de échte leverancier. (None, []) als er geen label gevonden is.
    """
    for i, ln in enumerate(lines):
        m = SUPPLIER_LABEL_RE.match(ln)
        if not m:
            continue
        blok = []
        rest = (m.group(1) or "").strip()
        if rest:
            blok.append(rest)
        for nxt in lines[i + 1:]:
            s = nxt.strip()
            if not s or CUSTOMER_LABEL_RE.match(s) or _META_LABEL_RE.match(s):
                break
            blok.append(s)
            if len(blok) >= 6:
                break
        for j, b in enumerate(blok):
            naam = _schoon_leveranciernaam(b)
            if naam and len(naam) > 1 and not WATERMARK_RE.search(naam):
                return naam, blok[j + 1:]
        return None, []
    return None, []


def _vul_leverancier_adres(lev, adresregels):
    """Vul address1/zipcode/city op basis van losse adresregels (NL-stijl).
    KvK/BTW/IBAN/e-mail/telefoon worden overgeslagen: die haalt parse_pdf
    globaal uit de volledige tekst."""
    for a in adresregels:
        a = a.strip()
        if not a:
            continue
        mzc = re.match(r"^\s*(\d{4}\s*[A-Z]{2})\s+([A-Za-z\.\-' ]+?)\s*$", a)
        if mzc and "city" not in lev:
            lev["zipcode"] = mzc.group(1).strip()
            lev["city"] = mzc.group(2).strip()
        elif "address1" not in lev and not re.search(
            r"@|kvk|btw|vat|iban|email|telefoon|\btel\b", a, re.IGNORECASE
        ):
            lev["address1"] = a


def parse_pdf(pdf_path):
    pdf_path = Path(pdf_path)
    with pdfplumber.open(pdf_path) as pdf:
        text = "\n".join((p.extract_text() or "") for p in pdf.pages)
        # Woorden (met x/y-posities) van de eerste pagina: nodig om twee-koloms
        # 'From ... Bill To'-layouts te ontwarren (kolommen lopen anders door
        # elkaar in de platte tekst).
        try:
            first_words = pdf.pages[0].extract_words() if pdf.pages else []
        except Exception:
            first_words = []

    out = {
        "valuta": "EUR",
        "prijzen_incl_btw": False,
        "leverancier": {},
        "regels": [],
        "_raw_text": text,
        "_confidence": {},
    }

    # ---------- factuurnummer ----------
    for pat in INVOICE_NR_RE_LIST:
        m = pat.search(text)
        if m:
            cand = m.group(1).strip().rstrip(".,;:")
            # vermijd dat we een bedrag matchen
            if not re.fullmatch(r"\d{1,3}([.,]\d{2,3})*[.,]\d{2}", cand):
                out["factuurnummer"] = cand
                out["_confidence"]["factuurnummer"] = 0.85
                break

    # ---------- datums (eerst gelabeld, dan heuristiek) ----------
    found_dates = []
    for pat, key in DATE_LABELED:
        m = pat.search(text)
        if m:
            d = _parse_date(m.group(1).strip())
            if d:
                out[key] = d
                out["_confidence"][key] = 0.9

    # Heuristiek-fallback: pak alle datums uit de tekst
    if "datum" not in out or "vervaldatum" not in out:
        for m in ALL_DATES_RE.finditer(text):
            d = _parse_date(m.group(1))
            if d and d not in found_dates:
                found_dates.append(d)
        if found_dates:
            if "datum" not in out:
                out["datum"] = found_dates[0]
                out["_confidence"]["datum"] = 0.6
            if "vervaldatum" not in out and len(found_dates) >= 2:
                out["vervaldatum"] = found_dates[1]
                out["_confidence"]["vervaldatum"] = 0.5

    # ---------- leverancier ----------
    lines = [ln.rstrip() for ln in text.splitlines() if ln.strip()]
    lev = out["leverancier"]

    # Voorkeur 1: expliciet label 'Leverancier:' / 'Afzender:' / 'Supplier:'.
    # Sterkste signaal op NL-facturen; negeert sample-/watermerkregels bovenaan
    # en het afnemer-blok, en pakt de échte leverancier.
    lbl_naam, lbl_adres = _leverancier_uit_label(lines)
    if lbl_naam:
        lev["company_name"] = lbl_naam
        out["_confidence"]["leverancier_naam"] = 0.95
        _vul_leverancier_adres(lev, lbl_adres)

    # Voorkeur 2: twee-koloms 'From ... Bill To'-layout ontwarren via x-posities.
    # Dit voorkomt dat losse watermerk-letters of de Bill-To-naam als leverancier
    # worden opgepikt.
    if not lev.get("company_name"):
        kol_naam, kol_adres = _leverancier_uit_kolommen(first_words)
        if kol_naam:
            lev["company_name"] = kol_naam
            out["_confidence"]["leverancier_naam"] = 0.9
            _vul_leverancier_adres(lev, kol_adres)

    SKIP_WORDS = re.compile(
        r"^(VERKOOPFACTUUR|INKOOPFACTUUR|CREDITFACTUUR|CREDITNOTA|FACTUUR|INVOICE|"
        r"From|Bill\s*to|Ship\s*to|Sold\s*to|Verkoper|Afzender|Klant|Afnemer|Customer|"
        r"Date|Datum|Factuurdatum|Factuurnummer|Vervaldatum|Due\s*Date|"
        r"Aan|To|#|Betreft|Reference|Pagina)\b|^#",
        re.IGNORECASE,
    )
    # Twee-koloms 'From ... Bill To'-layouts belanden soms als één tekstregel.
    # Zo'n regel (of een naam waarin 'Bill To' opduikt) is nooit de leverancier.
    BILLTO_RE = re.compile(r"\bbill\s*to\b|\bship\s*to\b", re.IGNORECASE)

    if lines and not lev.get("company_name"):
        # Loop door eerste 8 regels, pak eerste regel die niet gezicht hoort te zijn
        for ln in lines[:8]:
            ln_strip = ln.strip()
            if not ln_strip:
                continue
            if SKIP_WORDS.search(ln_strip):
                continue
            # Losse watermerk-letters ('S', 'A', 'M', ...) zijn geen leverancier.
            if len(ln_strip) <= 2 and ln_strip.replace(".", "").isalpha():
                continue
            if KVK_RE.search(ln_strip) or BTW_RE.search(ln_strip) or IBAN_RE.search(ln_strip):
                continue
            # niet alleen cijfers/leestekens
            if re.match(r"^[\d\s\.\,\-\/€:]+$", ln_strip):
                continue
            # niet alleen postcode-stijl
            if re.match(r"^\s*\d{4}\s*[A-Z]{2}\s*$", ln_strip):
                continue
            naam = ln_strip[:100]
            # Doc-markers ("VERKOOPFACTUUR"/"FACTUUR"/"INVOICE"/...) eraf — die
            # belanden soms op dezelfde regel als de leveranciersnaam doordat
            # naast elkaar geplaatste tabelcellen in de PDF op één tekstregel
            # terechtkomen, of een kale doc-type-regel staat bovenaan.
            naam = _schoon_leveranciernaam(naam)
            if not naam:
                continue
            # Sample-/watermerk-/disclaimerregels zijn nooit de leverancier
            # (bv. 'SAMPLE - GEEN ECHTE FACTUUR' bovenaan een testfactuur).
            if WATERMARK_RE.search(naam):
                continue
            # 'From'/'Bill To'-koprijen of restanten daarvan zijn geen leverancier.
            if BILLTO_RE.search(naam) or naam.strip().lower() in ("from", "bill to", "ship to"):
                continue
            lev["company_name"] = naam
            out["_confidence"]["leverancier_naam"] = 0.7
            break

        # Adres-block parser
        for ln in lines[1:12]:
            ln_strip = ln.strip()
            if SKIP_WORDS.search(ln_strip):
                break
            if KVK_RE.search(ln_strip) or BTW_RE.search(ln_strip) or IBAN_RE.search(ln_strip):
                continue
            # NL postcode + plaats
            mzc = re.match(r"^\s*(\d{4}\s*[A-Z]{2})\s+([A-Za-z\.\-' ]+?)\s*$", ln_strip)
            if mzc:
                lev["zipcode"] = mzc.group(1).strip()
                lev["city"] = mzc.group(2).strip()
                continue
            # Internationaal postcode + plaats
            mzc = re.match(r"^\s*([0-9A-Z\-]{3,10})\s+([A-Za-z\.\-' ]+?)\s*$", ln_strip)
            if mzc and "address1" in lev and "city" not in lev:
                lev["zipcode"] = mzc.group(1).strip()
                lev["city"] = mzc.group(2).strip()
                continue
            # Land
            if re.match(r"^(NL|BE|DE|FR|US|UK|GB|ES|IT|LU)$", ln_strip):
                lev["country"] = ln_strip
                continue
            if "address1" not in lev and ln_strip != lev.get("company_name"):
                lev["address1"] = ln_strip

    m = KVK_RE.search(text)
    if m:
        lev["chamber_of_commerce"] = m.group(1)
    m = BTW_RE.search(text)
    if m:
        # Voorkom dat we 'BTW (21%)' matchen
        cand = m.group(1)
        if not re.fullmatch(r"\d{1,2}", cand):
            lev["tax_number"] = cand
    m = IBAN_RE.search(text)
    if m:
        lev["iban"] = m.group(1).upper().replace(" ", "")
    if "country" not in lev:
        lev["country"] = "NL"

    # ---------- BTW verlegd ----------
    if BTW_VERLEGD_RE.search(text):
        out["btw_verlegd"] = True

    # ---------- bedragen ----------
    m = SUBTOTAAL_LABELED.search(text)
    if m:
        out["totaal_excl_btw"] = _clean_amount(m.group(1))

    # Probeer expliciet 'Totaal' / 'Total' / 'Balance Due' / 'Amount Due'
    incl_candidates = []
    for pat in TOTAAL_LABELED:
        for m in pat.finditer(text):
            incl_candidates.append(_clean_amount(m.group(1)))
    if incl_candidates:
        out["totaal_incl_btw"] = max(incl_candidates)

    m = BTW_LABELED.search(text)
    if m:
        out["btw_bedrag"] = _clean_amount(m.group(2))
        out["_btw_pct_gevonden"] = int(m.group(1))
    else:
        # Fallback: alleen percentage zonder bedrag (in meta-blok)
        m = BTW_META_RE.search(text)
        if m:
            try:
                out["_btw_pct_gevonden"] = int(m.group(1))
            except (TypeError, ValueError):
                pass

    # ---------- HEURISTIEK FALLBACK voor totaal ----------
    # Als we nog geen totaal hebben: pak alle bedragen op de pagina,
    # neem het grootste. Bijna altijd is dat het factuurtotaal.
    if "totaal_incl_btw" not in out:
        all_amounts = [_clean_amount(m.group(1)) for m in ALL_AMOUNTS_RE.finditer(text)]
        all_amounts = [a for a in all_amounts if a > 0]
        if all_amounts:
            out["totaal_incl_btw"] = max(all_amounts)
            out["_confidence"]["totaal_heuristiek"] = 0.5

    # ---------- regels parsen ----------
    out["regels"] = _parse_regels(text)

    # Vul btw_percentage in regels aan op basis van wat we vonden
    if out["regels"]:
        pct_default = out.get("_btw_pct_gevonden")
        if pct_default is not None:
            for r in out["regels"]:
                if r.get("btw_percentage") is None:
                    r["btw_percentage"] = pct_default
        if out.get("btw_verlegd"):
            for r in out["regels"]:
                r["btw_percentage"] = 0

    # ---------- FALLBACK: synthetiseer 1 regel uit totaal ----------
    if not out["regels"]:
        excl = out.get("totaal_excl_btw")
        incl = out.get("totaal_incl_btw")
        btw = out.get("btw_bedrag")

        if excl is None and incl is not None and btw is not None:
            excl = round(incl - btw, 2)
        elif excl is None and incl is not None:
            # Probeer btw af te leiden uit btw-percentage indien bekend
            pct = out.get("_btw_pct_gevonden")
            if pct:
                excl = round(incl / (1 + pct / 100), 2)
            else:
                excl = incl

        pct = None
        if out.get("btw_verlegd"):
            pct = 0
        elif btw is not None and excl:
            ratio = btw / excl * 100
            for std in (21, 9, 6, 0):
                if abs(ratio - std) < 1.5:
                    pct = std
                    break
        elif out.get("_btw_pct_gevonden") is not None:
            pct = out["_btw_pct_gevonden"]

        if excl and excl > 0:
            naam_in_omschrijving = (
                lev.get("company_name") or
                f"Factuur {out.get('factuurnummer', '')}".strip() or
                "Inkoopfactuur"
            )
            out["regels"] = [{
                "omschrijving": f"Inkoop {naam_in_omschrijving}".strip()[:200],
                "aantal": 1,
                "prijs_per_stuk": float(excl),
                "btw_percentage": pct if pct is not None else 21,
            }]
            out["_confidence"]["regels_synthetisch"] = 0.5

    # Zekerheidsfactuurnummer: als nog steeds niet gevonden, gebruik bestandsnaam
    if not out.get("factuurnummer"):
        name = pdf_path.stem
        out["factuurnummer"] = re.sub(r"[^A-Za-z0-9\-/_]", "-", name)[:30]
        out["_confidence"]["factuurnummer_uit_bestandsnaam"] = 0.3

    # Zekerheidsleverancier: als geen company_name, gebruik bestandsnaam-prefix
    if not lev.get("company_name"):
        name = pdf_path.stem
        # neem eerste deel voor evt cijfers (bv "Sligro" uit "Sligro-2025-001")
        prefix = re.split(r"[\d_\-]", name, 1)[0].strip()
        if not prefix or len(prefix) < 2:
            prefix = "Onbekende leverancier"
        lev["company_name"] = prefix[:100]
        out["_confidence"]["leverancier_uit_bestandsnaam"] = 0.2

    # Zekerheidsdatum: vandaag als laatste redmiddel
    if not out.get("datum"):
        out["datum"] = datetime.now().date().isoformat()
        out["_confidence"]["datum_fallback_vandaag"] = 0.2

    return out


REGEL_HEADER_RE = re.compile(
    r"\b(Omschrijving|Description|Item|Artikel|Product|Werkzaamheden)\b.*?\b(Aantal|Qty|Quantity|Hoeveelheid|Amount|Bedrag|Total)\b",
    re.IGNORECASE,
)
SUBTOTAL_LINE_RE = re.compile(r"\b(Subtotaal|Subtotal|Totaal|Total|BTW|VAT)\b", re.IGNORECASE)


def _parse_regels(text):
    regels = []
    lines = text.splitlines()

    start = end = None
    for i, ln in enumerate(lines):
        if REGEL_HEADER_RE.search(ln):
            start = i + 1
            break

    if start is None:
        return regels

    for i in range(start, len(lines)):
        if SUBTOTAL_LINE_RE.search(lines[i]):
            end = i
            break
    if end is None:
        end = len(lines)

    # Bedrag-prefix: € of EUR (eventueel meerdere keren in een regel)
    CUR = r"(?:€|EUR|EUR\.?)?"
    # 5-kolom: Omschrijving Aantal Tarief Korting Regeltotaal
    line_re_5col = re.compile(
        rf"^(?P<om>.+?)\s+(?P<qty>\d+(?:[.,]\d+)?)\s+{CUR}\s*(?P<price>[\d\.\,]+)\s+(?P<korting>\d+(?:[.,]\d+)?)\s*%\s+{CUR}\s*(?P<total>[\d\.\,]+)\s*$"
    )
    # 4-kolom: Omschrijving Aantal Prijs Totaal
    line_re = re.compile(
        rf"^(?P<om>.+?)\s+(?P<qty>\d+(?:[.,]\d+)?)\s+{CUR}\s*(?P<price>[\d\.\,]+)\s+{CUR}\s*(?P<total>[\d\.\,]+)\s*$"
    )
    # 2-kolom: Omschrijving + Totaal
    line_re_2col = re.compile(
        rf"^(?P<om>.+?)\s+{CUR}\s*(?P<total>[\d\.\,]+)\s*$"
    )

    for ln in lines[start:end]:
        ln = ln.strip()
        if not ln:
            continue
        # Probeer eerst 5-kolom (met Korting)
        m = line_re_5col.match(ln)
        if m:
            qty = int(_clean_amount(m.group("qty"))) or 1
            price = _clean_amount(m.group("price"))
            korting_pct = _clean_amount(m.group("korting"))
            # Effectieve prijs per stuk na korting
            effective_price = price * (1 - korting_pct / 100) if korting_pct else price
            regels.append({
                "omschrijving": m.group("om").strip(),
                "aantal": qty,
                "prijs_per_stuk": round(effective_price, 2),
                "btw_percentage": None,
            })
            continue
        # Dan 4-kolom (zonder korting)
        m = line_re.match(ln)
        if m:
            regels.append({
                "omschrijving": m.group("om").strip(),
                "aantal": int(_clean_amount(m.group("qty"))) or 1,
                "prijs_per_stuk": _clean_amount(m.group("price")),
                "btw_percentage": None,
            })
            continue
        # 2-kolom fallback (omschrijving + totaal)
        m = line_re_2col.match(ln)
        if m:
            tot = _clean_amount(m.group("total"))
            if tot > 0:
                regels.append({
                    "omschrijving": m.group("om").strip(),
                    "aantal": 1,
                    "prijs_per_stuk": tot,
                    "btw_percentage": None,
                })

    return regels


if __name__ == "__main__":
    import json, sys
    if len(sys.argv) != 2:
        print("usage: python pdf_parser.py /pad/naar/factuur.pdf")
        sys.exit(2)
    result = parse_pdf(sys.argv[1])
    safe = {k: v for k, v in result.items() if not k.startswith("_raw")}
    print(json.dumps(safe, indent=2, ensure_ascii=False))
