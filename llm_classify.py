"""
llm_classify.py - grootboek-classificatie via een LLM (Claude of OpenAI).

Doel: de echte sprong naar 95%. De LLM krijgt de ECHTE grootboekenlijst van
het boekhoudpakket plus de regel + leverancier (en eerdere correcties als
voorbeelden) en kiest exact één rekening uit die lijst, met een zekerheid.

Geen extra libraries nodig: we praten direct met de API via `requests`.

Sleutel: zet in .env ÉÉN van beide:
    ANTHROPIC_API_KEY=sk-ant-...      (Claude Haiku 4.5, default)
    OPENAI_API_KEY=sk-...             (GPT-4o-mini)
Optioneel:
    LLM_MODEL=...                     (overschrijf het model)

Als er geen sleutel is of de call faalt, geeft classificeer() None terug en
valt de aanroeper terug op de slimme trefwoord-regels.
"""
from __future__ import annotations

import html
import json
import os
import re

import requests

DEFAULT_MODELS = {
    "anthropic": "claude-haiku-4-5-20251001",
    "openai": "gpt-4o-mini",
}


def _provider():
    if os.environ.get("ANTHROPIC_API_KEY"):
        return "anthropic"
    if os.environ.get("OPENAI_API_KEY"):
        return "openai"
    return None


def is_available() -> bool:
    return _provider() is not None


def _ledger_naam(l):
    return html.unescape(l.get("path_name") or l.get("name") or "")


def _bouw_prompt(ledgers, omschrijving, leverancier, voorbeelden):
    namen = "\n".join("- " + _ledger_naam(l) for l in ledgers)
    vb = ""
    if voorbeelden:
        regels = "\n".join(f'- "{o}" -> {a}' for o, a in voorbeelden[:15])
        vb = ("\nEerdere handmatige correcties (volg deze waar van toepassing):\n"
              + regels + "\n")
    return (
        "Je bent een Nederlandse boekhoud-assistent voor een bouwbedrijf. "
        "Kies voor de onderstaande inkoopfactuurregel de juiste grootboekrekening "
        "UIT DE LIJST hieronder. Kies de meest specifieke passende rekening; "
        "vermijd verzamelrekeningen (zoals 'Diversen' of 'Overige kosten') als er "
        "een specifiekere past. Boek NOOIT op 'Betalingsverschillen' of 'Rente'. "
        "Als je echt twijfelt, geef dan een lage confidence.\n\n"
        "Beschikbare grootboekrekeningen:\n" + namen + "\n"
        + vb +
        f'\nFactuurregel: "{omschrijving}"\n'
        f'Leverancier: "{leverancier or "onbekend"}"\n\n'
        'Antwoord met UITSLUITEND JSON, geen extra tekst:\n'
        '{"account": "<exacte naam uit de lijst>", "confidence": <0.0-1.0>, '
        '"reason": "<korte uitleg in het Nederlands>"}'
    )


def _call_anthropic(model, prompt, timeout):
    r = requests.post(
        "https://api.anthropic.com/v1/messages",
        headers={
            "x-api-key": os.environ["ANTHROPIC_API_KEY"],
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        },
        json={
            "model": model,
            "max_tokens": 300,
            "messages": [{"role": "user", "content": prompt}],
        },
        timeout=timeout,
    )
    r.raise_for_status()
    d = r.json()
    return "".join(
        b.get("text", "") for b in d.get("content", []) if b.get("type") == "text"
    )


def _call_openai(model, prompt, timeout):
    r = requests.post(
        "https://api.openai.com/v1/chat/completions",
        headers={
            "Authorization": "Bearer " + os.environ["OPENAI_API_KEY"],
            "Content-Type": "application/json",
        },
        json={
            "model": model,
            "max_tokens": 300,
            "temperature": 0,
            "messages": [{"role": "user", "content": prompt}],
        },
        timeout=timeout,
    )
    r.raise_for_status()
    return r.json()["choices"][0]["message"]["content"]


def _parse_json(text):
    if not text:
        return None
    m = re.search(r"\{.*\}", text, re.DOTALL)
    if not m:
        return None
    try:
        return json.loads(m.group(0))
    except ValueError:
        return None


def _match_ledger(ledgers, account_naam):
    if not account_naam:
        return None
    doel = html.unescape(account_naam).strip().lower()
    # exacte match op name of path_name
    for l in ledgers:
        if html.unescape(l.get("name") or "").strip().lower() == doel:
            return l
    for l in ledgers:
        if _ledger_naam(l).strip().lower() == doel:
            return l
    # substring (laatste segment van het pad)
    for l in ledgers:
        naam = html.unescape(l.get("name") or "").strip().lower()
        if naam and (naam in doel or doel in naam):
            return l
    return None


def classificeer(ledgers, omschrijving, leverancier="", voorbeelden=None, timeout=20):
    """Vraag de LLM om een grootboek. Retourneer dict (zie classify.py) of None."""
    prov = _provider()
    if not prov or not ledgers:
        return None
    model = os.environ.get("LLM_MODEL") or DEFAULT_MODELS[prov]
    prompt = _bouw_prompt(ledgers, omschrijving, leverancier, voorbeelden)
    try:
        text = _call_anthropic(model, prompt, timeout) if prov == "anthropic" \
            else _call_openai(model, prompt, timeout)
    except Exception:
        return None  # netwerk/auth/rate-limit: val terug op regels
    data = _parse_json(text)
    if not data:
        return None
    account = _match_ledger(ledgers, data.get("account"))
    if not account:
        return None
    try:
        conf = float(data.get("confidence", 0.7))
    except (TypeError, ValueError):
        conf = 0.7
    conf = max(0.0, min(1.0, conf))
    return {
        "account": account,
        "confidence": conf,
        "method": "llm",
        "reason": (data.get("reason") or "LLM-keuze.") + f" ({model})",
        "alternatives": [],
    }
