# AdminBoeker

AI-boekhouder voor Moneybird. Sleep een PDF van een inkoopfactuur, en hij wordt volledig automatisch geboekt — leverancier herkend of aangemaakt, regels en BTW gesplitst, originele PDF als bijlage gekoppeld.

## Snel starten (Windows)

1. Zorg dat Python 3.10 of nieuwer geinstalleerd is (`python --version`)
2. Open een terminal in deze folder, of dubbelklik op `start.bat`
3. Open de browser op **http://localhost:5000**
4. Sleep een PDF op de drop-zone

## Wat er gebeurt

```
PDF                pdfplumber + parser            boek_agent.py
sleep    -->       leverancier extracten   -->    boek in Moneybird
                   datums, regels, BTW            PDF als bijlage
```

## Mappenstructuur

```
app.py               Webserver (Flask)
moneybird.py         API-client
boek_agent.py        Boek-orchestratie
pdf_parser.py        PDF -> gestructureerde data
templates/index.html Frontend (drag & drop UI)
tests/               Synthetische dataset + accuracy-evaluator
.env                 API-key + administratie-ID (NIET delen)
requirements.txt     Python dependencies
```

## Veiligheid

`.env` bevat de API-key. Niet in git, niet in chat, niet in screenshots. Vervang als hij ooit per ongeluk gedeeld is.

## Roadmap

- Slimmere parser (LLM-fallback voor lastige layouts)
- Verkoopfacturen valideren voor verzending
- Bankboek + matching tegen openstaande facturen
- Continue monitoring (prijsverschillen, dubbele facturen)
- Multi-tenant dashboard voor administratiekantoren
