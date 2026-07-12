"""Ponto de entrada: raspa um concelho, grava o JSON e carrega-o no SQLite.

Uso: python scripts/run_scrape.py [concelho]
"""
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from house_tracker.scraping.sapo import scrape_sapo_site
from house_tracker.storage.sqlite_store import json_to_sqlite


def main(concelho="amadora"):
    propriedades = scrape_sapo_site(concelho)
    if not propriedades:
        return

    date_format = date.today().strftime("%Y_%m_%d")
    json_file = f"data/json/propriedades_sapo_{concelho}_{date_format}.json"
    json_to_sqlite(json_file)


if __name__ == "__main__":
    concelho_arg = sys.argv[1] if len(sys.argv) > 1 else "amadora"
    main(concelho_arg)
