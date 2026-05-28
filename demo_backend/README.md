# AdminBooker — Demo backend

Slanke Flask-backend voor de live demo op **adminbooker.com**. Ontvangt een
PDF, retourneert de geëxtraheerde factuurdata. **Boekt niets in Moneybird.**

## Wat zit er wel/niet in

| Wel | Niet |
|---|---|
| `POST /api/demo-extract` — PDF → JSON | Moneybird-koppeling (zit in de hoofdapp) |
| `GET /api/health` | Authenticatie / accounts |
| Rate-limit (20 req / 10 min per IP) | Persistente opslag |
| CORS voor `adminbooker.com` + localhost | Telemetrie / tracking |
| Auto-cleanup van geüploade PDFs | Cookies |

## Lokaal draaien

```bash
cd demo_backend
pip install -r requirements.txt
python app.py
# -> http://localhost:8080/api/health
```

Test:

```bash
curl -F "file=@../tests/dataset/factuur_01.pdf" http://localhost:8080/api/demo-extract
```

## Deployen (Render)

Zie `../DEPLOY.md`. Korte versie: zet repo op GitHub, op Render kies "New →
Blueprint" en wijs naar deze repo — `render.yaml` doet de rest.

## Configuratie

Pas `ALLOWED_ORIGINS` in `app.py` aan als je vanaf een andere domein wilt
testen (bijvoorbeeld een Netlify deploy-preview-URL).
