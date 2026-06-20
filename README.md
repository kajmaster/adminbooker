# AdminBooker

Automatische bouwadministratie in Rompslomp. Sleep een PDF van een inkoopfactuur, en hij wordt volledig automatisch geboekt — leverancier herkend of aangemaakt, regels en BTW (ook verlegd) gesplitst, originele PDF als bijlage gekoppeld. Geen knoppen, geen review-stap.

Gemaakt voor de bouw: onderaannemers, materiaalfacturen en bouwmarkt-bonnen, in NL en EN. Twijfelgevallen boekt hij niet blind door, maar zet hij apart in 'Te controleren'.

## Snel starten (Windows)

1. Zorg dat Python 3.10 of nieuwer geïnstalleerd is (`python --version`)
2. Kopieer `.env.example` naar `.env` en vul je Rompslomp-token in
3. Open een terminal in deze folder, of dubbelklik op `start.bat`
4. Open de browser op **http://localhost:5000**
5. Sleep een PDF op de drop-zone

## Wat er gebeurt

```
PDF                pdfplumber + parser            boek_agent.py
sleep    -->       leverancier extracten   -->    boek in Rompslomp
                   datums, regels, BTW            PDF als bijlage
```

## Providers

AdminBooker werkt met een provider-laag (`providers/`). De actieve provider
wordt gekozen via `ACCOUNTING_PROVIDER` in `.env` (default: `rompslomp`).

- **Rompslomp** — actieve provider, gericht op de bouwadministratie
- **Exact Online** — skeleton, OAuth-flow nog niet uitgewerkt
- **Moneybird** — verouderd, verborgen in de UI (code blijft aanwezig)

## Mappenstructuur

```
app.py                        Webserver (Flask)
providers/                    Provider-laag (Rompslomp, Exact, Moneybird)
providers/rompslomp_provider.py  Rompslomp API-client
boek_agent.py                 Boek-orchestratie
pdf_parser.py                 PDF -> gestructureerde data
templates/index.html          Frontend (drag & drop UI)
web/                          Landingspagina (Netlify -> adminbooker.com)
tests/                        Synthetische dataset + accuracy-evaluator
.env                          API-token (NIET delen)
requirements.txt              Python dependencies
```

## Veiligheid

`.env` bevat het API-token. Niet in git, niet in chat, niet in screenshots. Vervang het als het ooit per ongeluk gedeeld is.

## Roadmap

- Slimmere parser (LLM-fallback voor lastige layouts)
- Verkoopfacturen valideren voor verzending
- Bankboek + matching tegen openstaande facturen
- Continue monitoring (prijsverschillen, dubbele facturen)
- Multi-tenant dashboard voor administratiekantoren
