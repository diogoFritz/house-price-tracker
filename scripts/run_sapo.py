"""Ponto de entrada: descarrega e extrai os imóveis de um concelho no Sapo Casa.

Retomável: se já houver páginas descarregadas em sapo/html/<concelho>/, são
reaproveitadas em vez de repedidas. Usa --forcar-novo para limpar e recomeçar.

Uso: python scripts/run_sapo.py [concelho] [--forcar-novo]
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from house_tracker.scraping.sapo import download_sapo_all, parse_sapo_all


def main(concelho="amadora", forcar_novo=False):
    download_sapo_all(concelho, forcar_novo=forcar_novo)
    parse_sapo_all(concelho)


if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    concelho_arg = args[0] if args else "amadora"
    forcar_novo_arg = "--forcar-novo" in sys.argv
    main(concelho_arg, forcar_novo_arg)
