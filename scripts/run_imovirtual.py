"""Ponto de entrada: extrai todos os imóveis de um concelho no Imovirtual.

Retomável: se a extração for interrompida (erro, bloqueio/captcha), corre o
mesmo comando outra vez para retomar a partir da última página em falta.

Uso: python scripts/run_imovirtual.py [concelho] [distrito]
     (distrito é opcional; por omissão usa o mesmo nome do concelho)
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from house_tracker.scraping.imovirtual import fetch_all_listings


def main(concelho="lisboa", distrito=None):
    fetch_all_listings(concelho=concelho, distrito=distrito)


if __name__ == "__main__":
    concelho_arg = sys.argv[1] if len(sys.argv) > 1 else "lisboa"
    distrito_arg = sys.argv[2] if len(sys.argv) > 2 else None
    main(concelho_arg, distrito_arg)
