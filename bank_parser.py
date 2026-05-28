"""
bank_parser.py - lees CSV of MT940 bankbestand uit en geef lijst transacties.

Output structuur (lijst dicts):
[
  {
    "datum": "2026-01-15",            # YYYY-MM-DD
    "bedrag": -49.95,                  # negatief = uitgaand, positief = inkomend
    "omschrijving": "Hosting B.V. ...",
    "tegen_iban": "NL91ABNA0417164300",  # optioneel
    "tegen_naam": "Hosting B.V.",        # optioneel
  },
  ...
]

Ondersteunt:
- CSV (auto-detect delimiter en kolommen via header-keywords)
- MT940 (standaard NL bankafschrift-formaat)
"""
from __future__ import annotations

import csv
import io
import re
from datetime import datetime
from pathlib import Path


# ---------- helpers ----------

def _clean_amount(s):
    if s is None:
        return 0.0
    s = str(s).strip().replace("€", "").replace("EUR", "").strip()
    # negatief teken kan zowel "-100,00" als "100,00-" zijn (sommige NL banken)
    trailing_minus = s.endswith("-")
    if trailing_minus:
        s = s[:-1].strip()
    m = re.search(r"[-+]?[\d\.\,]+", s)
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
        val = float(num)
        return -val if trailing_minus else val
    except ValueError:
        return 0.0


def _parse_date_any(s):
    if not s:
        return None
    s = str(s).strip()
    for fmt in ("%Y-%m-%d", "%d-%m-%Y", "%d/%m/%Y", "%Y%m%d", "%d.%m.%Y", "%Y/%m/%d"):
        try:
            return datetime.strptime(s, fmt).date().isoformat()
        except ValueError:
            continue
    return None


# ---------- CSV ----------

# Kolomnamen die meestal gebruikt worden door NL banken (lowercase match)
COL_KEYWORDS = {
    "datum": ["datum", "date", "boekdatum", "transactiedatum", "valuta"],
    "bedrag": ["bedrag", "amount", "bedrag (eur)", "amount (eur)", "transactiebedrag"],
    "omschrijving": ["omschrijving", "description", "mededelingen", "mededeling", "name / description", "narrative"],
    "tegen_iban": ["tegenrekening", "iban tegenrekening", "counter account", "tegenpartij iban", "iban", "counterparty iban"],
    "tegen_naam": ["naam tegenrekening", "tegenpartij", "counter name", "naam", "name", "counterparty"],
    "af_bij": ["af bij", "af/bij", "debit/credit", "type"],
}


def _detect_delimiter(text):
    candidates = [";", ",", "\t", "|"]
    counts = {c: text.count(c) for c in candidates}
    return max(counts, key=counts.get)


def _find_col(headers, keywords):
    for i, h in enumerate(headers):
        h_norm = h.strip().lower()
        for k in keywords:
            if k in h_norm:
                return i
    return None


def parse_csv(content):
    """Parse CSV-tekst naar lijst transacties. Auto-detect kolommen."""
    delim = _detect_delimiter(content[:2000])
    reader = csv.reader(io.StringIO(content), delimiter=delim)
    rows = list(reader)
    if len(rows) < 2:
        return []

    headers = rows[0]
    col_idx = {k: _find_col(headers, v) for k, v in COL_KEYWORDS.items()}

    out = []
    for row in rows[1:]:
        if not any(c.strip() for c in row):
            continue
        # Haal velden veilig op
        def get(key):
            idx = col_idx.get(key)
            if idx is None or idx >= len(row):
                return None
            return row[idx]

        datum = _parse_date_any(get("datum"))
        bedrag = _clean_amount(get("bedrag"))
        # Af/bij kolom maakt bedrag negatief als 'Af' / 'D' / 'Debit'
        afbij = (get("af_bij") or "").strip().lower()
        if afbij and bedrag > 0:
            if afbij in ("af", "debet", "debit", "d", "out"):
                bedrag = -bedrag

        if not datum or bedrag == 0:
            continue

        out.append({
            "datum": datum,
            "bedrag": round(bedrag, 2),
            "omschrijving": (get("omschrijving") or "").strip()[:200],
            "tegen_iban": (get("tegen_iban") or "").strip().upper().replace(" ", "") or None,
            "tegen_naam": (get("tegen_naam") or "").strip() or None,
        })
    return out


# ---------- MT940 ----------

# MT940 referentie: regels beginnen met :tag:waarde
#  :20:  Transaction reference
#  :25:  Account identification (IBAN)
#  :60F: Opening balance
#  :61:  Transaction (datum, bedrag, debit/credit)
#  :86:  Mededelingen / extra info
#  :62F: Closing balance


def parse_mt940(content):
    """Eenvoudige MT940-parser - regels :61: + :86: combineren tot transactie."""
    # MT940 gebruikt vaak \r\n; normaliseer
    content = content.replace("\r\n", "\n").replace("\r", "\n")
    lines = content.split("\n")

    out = []
    current = None
    info_lines = []

    def flush():
        if current:
            current["omschrijving"] = " ".join(info_lines).strip()[:200]
            # Tegen-IBAN soms in :86: als /IBAN/XXXXX
            m = re.search(r"\b([A-Z]{2}\d{2}[A-Z0-9]{4}\d{7,16})\b",
                          current["omschrijving"])
            if m:
                current["tegen_iban"] = m.group(1)
            out.append(current)

    for raw in lines:
        line = raw.rstrip()
        # :61: 2601150115C49,95N123NONREF
        m = re.match(r":61:(\d{6})(\d{4})?([CD])R?([\d\.,]+)", line)
        if m:
            flush()
            info_lines = []
            yymmdd = m.group(1)
            try:
                dt = datetime.strptime(yymmdd, "%y%m%d").date().isoformat()
            except ValueError:
                dt = None
            sign = 1 if m.group(3) == "C" else -1
            amount = _clean_amount(m.group(4)) * sign
            current = {
                "datum": dt,
                "bedrag": round(amount, 2),
                "omschrijving": "",
                "tegen_iban": None,
                "tegen_naam": None,
            }
        elif line.startswith(":86:") and current is not None:
            info_lines.append(line[4:].strip())
        elif current is not None and not line.startswith(":") and info_lines:
            # Vervolgregel van :86:
            info_lines.append(line.strip())

    flush()
    # Verwijder transacties zonder datum
    return [t for t in out if t["datum"]]


# ---------- Hoofdfunctie ----------

def parse_bank_file(path):
    """Detect formaat en parse. Retourneer lijst transacties."""
    p = Path(path)
    ext = p.suffix.lower()
    try:
        text = p.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        text = p.read_text(encoding="latin-1")

    if ext in (".csv", ".tsv", ".txt"):
        return parse_csv(text)
    if ext in (".mt940", ".sta", ".940"):
        return parse_mt940(text)
    # Auto-detect: MT940 begint vaak met :20: of bevat :61:
    if ":61:" in text[:5000] or text.startswith(":20:"):
        return parse_mt940(text)
    return parse_csv(text)


if __name__ == "__main__":
    import json, sys
    if len(sys.argv) != 2:
        print("usage: python bank_parser.py /pad/naar/bestand.csv")
        sys.exit(2)
    tx = parse_bank_file(sys.argv[1])
    print(json.dumps(tx, indent=2, ensure_ascii=False))
    print(f"\n{len(tx)} transacties gevonden")
