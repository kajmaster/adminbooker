# Deployment — snelle versie (Netlify + Namecheap DNS)

Geen GitHub, geen Render, geen backend. Alleen de landingspagina live krijgen op `adminbooker.com`. Drie stappen, ~20 minuten.

> De `demo_backend/` folder blijft staan voor later — zodra je de echte demo wilt aanzetten, zie `demo_backend/README.md`.

---

## Stap 1 — Drag-and-drop de site naar Netlify

1. Ga naar **[app.netlify.com/drop](https://app.netlify.com/drop)** (log in met je e-mail; account aanmaken is gratis).
2. Sleep de hele map **`web/`** uit `C:\Users\Kaj\Documents\Claude\Projects\AdminBooker\web` op het venster.
3. Wacht ~10 seconden. Je krijgt een URL als `https://random-koala-1234.netlify.app` — open 'm en check of de site er goed uit ziet.
4. Bovenin staat **"Get a custom domain"** of (Site overview) → **Site configuration → Domain management → Add a domain**.

> Update later? Sleep dezelfde map opnieuw op dezelfde site (Site overview → **Deploys** → drag-and-drop zone bovenaan). Hij overschrijft de oude versie.

---

## Stap 2 — Domein toevoegen in Netlify

1. **Site configuration → Domain management → Add a domain** → typ `adminbooker.com` → **Verify** → **Yes, add domain**.
2. Netlify zegt nu: "Check DNS configuration". Daar staan twee dingen die je over één minuut bij Namecheap intypt:
   - Voor **apex** (`adminbooker.com`): een **A-record naar `75.2.60.5`**
   - Voor **www**: een **CNAME naar `<jouw-site-naam>.netlify.app`** (Netlify toont de exacte naam)

> **Tip:** Netlify biedt ook "Use Netlify DNS" aan — dat is zelfs makkelijker (nameservers omzetten i.p.v. losse records). Maar daarvoor moet je Namecheap-nameservers vervangen door de 4 die Netlify geeft. Lees Stap 3-variant B als je dat liever doet.

---

## Stap 3 — DNS bij Namecheap aanpassen

### Variant A — Losse records (snelste, e-mail/MX blijft ongemoeid)

1. Namecheap → **Domain List** → naast `adminbooker.com` op **Manage** → tabblad **Advanced DNS**.
2. **Verwijder** de twee bestaande records:
   - `CNAME Record   www   parkingpage.namecheap.com`
   - `URL Redirect   @     http://www.adminbooker.co...`
3. **Voeg toe** (knop "Add New Record"):

   | Type     | Host | Value                                  | TTL       |
   |----------|------|----------------------------------------|-----------|
   | A Record | `@`  | `75.2.60.5`                            | Automatic |
   | CNAME    | `www`| `<jouw-site>.netlify.app.`             | Automatic |

   *(Vervang `<jouw-site>` door de naam die Netlify je gaf — zonder `https://`, met de punt aan het eind van de CNAME-waarde mag, hoeft niet.)*

4. Klik op het groene vinkje rechts om op te slaan.
5. Wacht 5–30 minuten. Refresh in Netlify de Domain-pagina — als hij groen wordt staat DNS.

### Variant B — Netlify DNS overnemen (makkelijkst als je niets anders met dit domein doet)

1. In Netlify klik je in de domain-stap op **"Set up Netlify DNS"**. Je krijgt 4 nameservers, bijvoorbeeld:
   ```
   dns1.p01.nsone.net
   dns2.p01.nsone.net
   dns3.p01.nsone.net
   dns4.p01.nsone.net
   ```
2. In Namecheap → **Domain List** → **Manage** → tabblad **Domain** → bij **Nameservers** kies **"Custom DNS"** en plak die 4 nameservers (één per regel) → groen vinkje.
3. Klaar. Netlify regelt verder zelf alle records.

---

## Daarna

- **HTTPS** komt vanzelf — Netlify zet binnen ~15 min een Let's Encrypt certificaat erop zodra DNS staat. Refresh de Domain-pagina, je ziet "HTTPS: Active".
- **Beta-aanmeldingen** komen binnen in Netlify → je site → **Forms**. Zet daar ook even **e-mail-notificaties** aan: **Forms → Settings → Form notifications → Add notification → email** naar `kajslier@gmail.com`.
- **Demo** staat in "Binnenkort beschikbaar"-modus. Wil je 'm later aanzetten? Open `web/index.html`, zet `DEMO_API_BASE` op de URL van een gedeployde backend, en sleep `web/` opnieuw op Netlify.

---

## Snelle troubleshoot

| Probleem | Oplossing |
|---|---|
| `adminbooker.com` opent nog steeds Namecheap-parking | DNS nog niet gepropageerd — wacht 15–30 min, of check op [dnschecker.org](https://dnschecker.org) of je A-record al overal staat |
| Wel `www.adminbooker.com` maar niet de apex | A-record op `@` ontbreekt of staat fout. Check Variant A stap 3 |
| Form-submissions komen niet binnen | In Netlify: **Site → Forms** — zie je `beta-signup` staan? Zo nee: **Site overview → Deploys → Trigger deploy → "Clear cache and deploy site"** |
| Wijziging in HTML lijkt niet door te komen | Browser-cache. Hard refresh met Ctrl+F5. Of check in Netlify of je nieuwe versie wel als laatste deploy staat |

Klaar.
