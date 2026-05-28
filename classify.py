"""
classify.py - grootboek-classificatie met zekerheid (confidence) + reden.

Dit is de fundering waar straks de LLM in schuift. De interface blijft gelijk:
classificeer_grootboek(...) geeft altijd terug:
    {
      "account": <ledger dict>,   # gekozen grootboek
      "confidence": 0.0..1.0,      # hoe zeker
      "method": "geheugen"|"regel"|"naam"|"default"|"noodgreep"|"llm",
      "reason": str,               # leesbare uitleg (voor sandbox + mail)
      "alternatives": [str, ...],  # kandidaat-namen bij twijfel
    }

Volgorde van zekerheid (hoog -> laag):
  1. correctie-geheugen  (eerder met de hand goedgezet)        -> 0.99
  2. trefwoord-regels    (woordgrens-match, pakket-onafhankelijk) -> 0.82
  3. directe naam-match op een expliciete hint                 -> 0.70
  4. verzamelrekening 'Diversen' als er niets matcht           -> 0.25  (twijfel!)
  5. noodgreep (vermijd betalingsverschillen/rente)            -> 0.10  (twijfel!)

Onder DREMPEL = "niet zeker" -> de boeking wordt gemarkeerd voor controle.

Belangrijk t.o.v. de oude kies_grootboek: trefwoorden worden nu met WOORDGRENZEN
gematcht voor korte woorden (<=4 letters). Daardoor matcht 'auto' niet langer op
'Automation' (de bug die consulting op autokosten liet belanden).
"""
from __future__ import annotations

import html
import re

# Hergebruik de trefwoord-regels en de ledger-tekst-helper uit boek_agent,
# zodat er één bron van waarheid is.
from boek_agent import _GROOTBOEK_REGELS, _ledger_tekst

import corrections as _corr
import llm_classify

DREMPEL = 0.5  # confidence < DREMPEL => markeer voor menselijke controle


def _correctie_voorbeelden(store, n=15):
    """Maak few-shot voorbeelden voor de LLM uit het correctie-geheugen."""
    if not store:
        return []
    entries = sorted(store.values(), key=lambda e: e.get("updated", ""), reverse=True)
    vb = []
    for e in entries[:n]:
        om = e.get("omschrijving")
        acc = e.get("account_name")
        if om and acc:
            vb.append((om, acc))
    return vb


def _match_kw(kw, text) -> bool:
    """Match een trefwoord. Korte woorden (<=4) alleen op woordgrens, zodat
    'auto' niet in 'automation' valt en 'bus' niet in 'business'."""
    kw = kw.strip()
    if not kw:
        return False
    if len(kw) <= 4:
        return re.search(r"\b" + re.escape(kw) + r"\b", text) is not None
    return kw in text


def _find_ledger(ledgers, patronen):
    for p in patronen:
        for l in ledgers:
            if p in _ledger_tekst(l):
                return l
    return None


def _by_id_or_name(ledgers, account_id, account_name):
    if account_id is not None:
        for l in ledgers:
            if str(l.get("id")) == str(account_id):
                return l
    nm = (account_name or "").lower().strip()
    if nm:
        for l in ledgers:
            if html.unescape(l.get("name") or "").lower() == nm:
                return l
            if html.unescape(_ledger_tekst(l)) == nm:
                return l
    return None


def _suggesties(ledgers, n=6):
    namen = []
    for l in ledgers:
        t = _ledger_tekst(l)
        if "betalingsverschillen" in t or "rente" in t:
            continue
        namen.append(html.unescape(l.get("name") or l.get("path_name") or ""))
        if len(namen) >= n:
            break
    return namen


def classificeer_grootboek(ledgers, omschrijving, leverancier="", hint="",
                           corrections=None):
    """Kies een kosten-grootboek met zekerheid + reden. Zie module-docstring."""
    if not ledgers:
        raise RuntimeError(
            "Geen inkoop-grootboekrekeningen beschikbaar in dit boekhoudpakket."
        )

    context = " ".join(
        str(x) for x in (hint, omschrijving, leverancier) if x
    ).lower().strip()

    # 1. correctie-geheugen (deterministisch, hoogste zekerheid)
    if corrections:
        hit = _corr.lookup(corrections, omschrijving, leverancier)
        if hit:
            l = _by_id_or_name(ledgers, hit.get("account_id"), hit.get("account_name"))
            if l:
                return {
                    "account": l, "confidence": 0.99, "method": "geheugen",
                    "reason": f"Eerder met de hand gezet op '{hit.get('account_name')}'.",
                    "alternatives": [],
                }

    # 2. LLM (indien sleutel aanwezig): de echte motor. Krijgt de correcties
    #    als voorbeelden mee. Bij geen sleutel/fout: None -> val terug op regels.
    if llm_classify.is_available():
        res = llm_classify.classificeer(
            ledgers, omschrijving, leverancier,
            voorbeelden=_correctie_voorbeelden(corrections),
        )
        if res:
            return res

    # 3. trefwoord-regels (woordgrens-bewust)
    if context:
        for trefwoorden, patronen in _GROOTBOEK_REGELS:
            kw = next((tw for tw in trefwoorden if _match_kw(tw, context)), None)
            if kw:
                l = _find_ledger(ledgers, patronen)
                if l:
                    naam = html.unescape(l.get("name") or "")
                    return {
                        "account": l, "confidence": 0.82, "method": "regel",
                        "reason": f"Trefwoord '{kw}' → {naam}.",
                        "alternatives": [],
                    }
                # regel matchte maar geen passend grootboek: blijf zoeken

    # 3. directe naam-match op een expliciete hint
    if hint:
        h = hint.lower().strip()
        for l in ledgers:
            naam = html.unescape(l.get("name") or "").lower()
            if naam and len(naam) > 3 and (h in naam or naam in h):
                return {
                    "account": l, "confidence": 0.70, "method": "naam",
                    "reason": f"Naam-match op hint '{hint}'.",
                    "alternatives": [],
                }

    # 4. verzamelrekening => lage zekerheid (twijfel)
    for patroon in ("diversen", "overige kosten", "algemene kosten",
                    "overige bedrijfskosten"):
        for l in ledgers:
            if patroon in _ledger_tekst(l):
                return {
                    "account": l, "confidence": 0.25, "method": "default",
                    "reason": "Geen duidelijke match; voorlopig op een verzamelrekening "
                              "gezet. Laat dit nakijken.",
                    "alternatives": _suggesties(ledgers),
                }

    # 5. noodgreep: vermijd betalingsverschillen/rente
    for l in ledgers:
        t = _ledger_tekst(l)
        if "betalingsverschillen" not in t and "verschillen" not in t \
                and "rente" not in t:
            return {
                "account": l, "confidence": 0.10, "method": "noodgreep",
                "reason": "Geen verzamelrekening gevonden; eerste bruikbare gekozen. "
                          "Laat dit nakijken.",
                "alternatives": _suggesties(ledgers),
            }
    return {
        "account": ledgers[0], "confidence": 0.10, "method": "noodgreep",
        "reason": "Laatste redmiddel.", "alternatives": _suggesties(ledgers),
    }
