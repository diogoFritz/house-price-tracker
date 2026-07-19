"""Ponto de entrada: extrai todos os imóveis de um concelho no Remax.

Retomável: se a extração for interrompida, corre o mesmo comando outra vez
para retomar a partir da última página em falta.

Uso: python scripts/run_remax.py [concelho]
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from house_tracker.scraping.remax import fetch_all_listings


def main(concelho="lisboa"):
    fetch_all_listings(concelho=concelho)


if __name__ == "__main__":
    concelho_arg = sys.argv[1] if len(sys.argv) > 1 else "lisboa"
    main(concelho_arg)
