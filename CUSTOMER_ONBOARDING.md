# Klant onboarden — playbook (eigen instance per bouwbedrijf)

Dit is de vaste route om een nieuwe klant live te zetten. Het model: **jij host
AdminBooker, elke klant krijgt een eigen afgeschermde instance** (eigen login,
eigen Rompslomp-token, eigen data-schijf). De klant hoeft **niets te installeren**
— ze openen een link in de browser en loggen in.

Tijd per klant: ~15 minuten, plus de kalibratie.

---

## Wat je vooraf nodig hebt

- Het **Rompslomp API-token** van de klant (zij maken het aan via *Mijn account →
  Mijn API tokens*). Company-id is optioneel — wordt automatisch ontdekt.
- Een **wachtwoord** dat je voor hun login kiest.
- Jouw eigen **OpenAI-sleutel** (of die van de klant).

---

## Stap 1 — Genereer de inloggegevens

In de projectmap:

```
python tools/new_customer.py --user admin --password "KiesEenWachtwoord!"
```

Je krijgt een kant-en-klaar env-blok (USERNAME, PASSWORD_HASH, SECRET_KEY,
ADMINBOOKER_DATA_DIR) plus lege Rompslomp-/OpenAI-velden om aan te vullen.
Niets wordt opgeslagen of verstuurd.

## Stap 2 — Maak een nieuwe Render-service

1. Render → **New → Web Service** → koppel repo `kajmaster/adminbooker`.
2. **Language: Python 3**. Build: `pip install -r requirements.txt`.
   Start-command leeg laten (de `Procfile` regelt gunicorn).
3. **Plan: Starter** (~€7/mnd — nodig voor de persistent disk).
4. Geef de service een herkenbare naam, bv. `adminbooker-bouwbedrijf-x`.

## Stap 3 — Voeg een disk toe

Settings → **Disks → Add Disk** → mount path `/var/data`, grootte `1 GB`.
Hier komen het correctie-geheugen, het postvak en de uploads — blijft behouden
bij elke redeploy.

## Stap 4 — Zet de environment-variabelen

Plak het blok uit stap 1 bij **Environment**, en vul aan:

| Variabele | Waarde |
|---|---|
| `USERNAME` | inlognaam (bv. `admin` of naam admin-kracht) |
| `PASSWORD_HASH` | uit het script |
| `SECRET_KEY` | uit het script — **vereist** als login aan staat |
| `ADMINBOOKER_DATA_DIR` | `/var/data` |
| `ROMPSLOMP_API_TOKEN` | token van de klant |
| `ROMPSLOMP_COMPANY_ID` | leeg laten = automatisch |
| `OPENAI_API_KEY` | jouw OpenAI-sleutel |

> Zodra `PASSWORD_HASH` gezet is, staat de login automatisch **aan** en is de
> instance afgeschermd. Laat je 'm leeg, dan is de instance open (handig voor je
> eigen demo).

## Stap 5 — Deploy

Render bouwt en geeft een URL (bv. `https://adminbooker-bouwbedrijf-x.onrender.com`).
Open 'm → je krijgt het loginscherm → log in met de gekozen gegevens.

*(Optioneel: koppel een net subdomein via Settings → Custom Domains.)*

## Stap 6 — Kalibreren op hun eigen boekhouding (de 95%-stap)

1. Open **/ijk** ("Leer van boekhouding") → klik **Leer van mijn boekhouding**.
   AdminBooker leest hun geboekte historie uit Rompslomp en leert per regel
   het juiste grootboek.
2. Draai in de **Sandbox** een recente batch facturen en loop ze na. Corrigeer
   waar nodig (hij onthoudt het).
3. Zet "live auto-boeken" pas in je hoofd op "akkoord" als hij op een apart,
   niet-geleerd setje **≥95%** haalt op alle velden. Dat is meteen je belofte
   naar de klant.

## Stap 7 — Overdragen

Geef de klant de **URL + login**. Klaar. Ze slepen hun facturen erin (of mailen
ze door), en lopen de twijfelgevallen na in **Te controleren**.

---

## Beslissing: wanneer "live"?

Pas auto-boeken aanzetten bij **≥95% op een hold-out-set** van hun eigen
facturen. Tot die tijd boekt hij niet blind door — alles loopt via
'Te controleren'. Dit is je risico-belofte: *"we zetten 'm pas aan als hij op
jullie eigen facturen 95% haalt."*

## Kosten per klant

~€7/mnd Render (Starter) + ~€0,25/mnd disk + een paar cent OpenAI per batch.
Reken je maandprijs hier ruim overheen.

## Jouw eigen demo open houden

Laat op de demo-instance `USERNAME` en `PASSWORD_HASH` gewoon **leeg** — dan is
er geen login en blijft 'ie direct bruikbaar.

---

## Sneller opzetten (optioneel) — Render Blueprint

Wil je niet elke keer handmatig klikken, dan kun je een `render.yaml` blueprint
gebruiken zodat service + disk + env-placeholders in één keer worden aangemaakt.
Voorbeeld (zet als `render.yaml` in een aparte deploy-branch om je bestaande
service niet te raken):

```yaml
services:
  - type: web
    name: adminbooker-klant
    runtime: python
    plan: starter
    buildCommand: pip install -r requirements.txt
    startCommand: gunicorn app:app --bind 0.0.0.0:$PORT --timeout 120 --workers 2
    disk:
      name: data
      mountPath: /var/data
      sizeGB: 1
    envVars:
      - key: ADMINBOOKER_DATA_DIR
        value: /var/data
      - key: USERNAME
        sync: false
      - key: PASSWORD_HASH
        sync: false
      - key: SECRET_KEY
        generateValue: true
      - key: ROMPSLOMP_API_TOKEN
        sync: false
      - key: OPENAI_API_KEY
        sync: false
```
