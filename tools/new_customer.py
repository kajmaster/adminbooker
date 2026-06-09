"""
new_customer.py - genereer de env-vars voor een nieuwe klant-instance.

Bij het onboarden van een bouwbedrijf draai je dit even, en je krijgt een
kant-en-klaar blok om in Render (Environment) te plakken: een uniek sessie-
geheim en een wachtwoord-hash voor hun login. De Rompslomp-gegevens vul je
daarna zelf aan met de tokens die de klant je geeft.

Gebruik:
    python tools/new_customer.py --user admin --password "KiesEenWachtwoord!"
    python tools/new_customer.py            # vraagt user + wachtwoord interactief

Niets wordt opgeslagen of verstuurd - alleen geprint in je terminal.
"""
from __future__ import annotations

import argparse
import getpass
import secrets

from werkzeug.security import generate_password_hash


def build_env(username: str, password: str) -> dict:
    return {
        "USERNAME": username,
        "PASSWORD_HASH": generate_password_hash(password),
        "SECRET_KEY": secrets.token_hex(32),
        "ADMINBOOKER_DATA_DIR": "/var/data",
        # Vul deze zelf aan met de gegevens van de klant / van jou:
        "ROMPSLOMP_API_TOKEN": "<plak hier het API-token van de klant>",
        "ROMPSLOMP_COMPANY_ID": "<optioneel - leeg laten = automatisch ontdekt>",
        "OPENAI_API_KEY": "<jouw OpenAI-sleutel>",
    }


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Genereer env-vars voor een nieuwe klant-instance.")
    ap.add_argument("--user", default=None, help="inlog-gebruikersnaam (default: admin)")
    ap.add_argument("--password", default=None, help="inlog-wachtwoord (anders interactief)")
    args = ap.parse_args(argv)

    username = args.user or input("Gebruikersnaam [admin]: ").strip() or "admin"
    password = args.password
    while not password:
        password = getpass.getpass("Wachtwoord voor de klant: ").strip()

    env = build_env(username, password)

    print()
    print("=" * 64)
    print(" Plak dit in Render -> Environment (per klant een eigen service):")
    print("=" * 64)
    for k, v in env.items():
        print(f"{k}={v}")
    print("=" * 64)
    print(f"\nLogin voor de klant:  gebruiker '{username}'  /  wachtwoord '{password}'")
    print("Let op: SECRET_KEY is vereist zodra login aan staat (anders breken")
    print("sessies bij meerdere workers). Vul nog de ROMPSLOMP_*-velden aan.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
