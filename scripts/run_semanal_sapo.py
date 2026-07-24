"""Extração semanal automática do Sapo, separada das restantes fontes por ser
bastante mais lenta (rate-limit 429 do casa.sapo.pt, com esperas de 90s entre
tentativas — ver house_tracker/scraping/sapo.py).

Uso: python scripts/run_semanal_sapo.py
"""
import logging
import sys
import time
import traceback
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from house_tracker.scraping.sapo import download_sapo_all, parse_sapo_all

CONCELHOS = [
    "lisboa", "amadora", "loures", "odivelas", "oeiras", "sintra",
    "alenquer", "arruda-dos-vinhos", "azambuja", "cadaval", "cascais",
    "lourinha", "mafra", "sobral-de-monte-agraco", "torres-vedras",
    "vila-franca-de-xira",
]

LOG_DIR = Path(__file__).resolve().parent.parent / "logs"


def main():
    LOG_DIR.mkdir(exist_ok=True)
    data_hoje = date.today().strftime("%Y%m%d")
    log_file = LOG_DIR / f"run_semanal_sapo_{data_hoje}.log"

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(levelname)s - %(message)s",
        handlers=[logging.FileHandler(log_file, encoding="utf-8"), logging.StreamHandler()],
    )

    logging.info(f"=== Início da extração semanal do Sapo ({data_hoje}) ===")
    inicio_geral = time.time()
    resumo = []

    for concelho in CONCELHOS:
        inicio = time.time()
        try:
            download_sapo_all(concelho)
            items = parse_sapo_all(concelho)
            duracao = time.time() - inicio
            resumo.append((concelho, len(items), None))
            logging.info(f"[OK] Sapo / {concelho}: {len(items)} imóveis ({duracao:.1f}s)")
        except Exception as e:
            duracao = time.time() - inicio
            resumo.append((concelho, None, str(e)))
            logging.error(f"[FALHOU] Sapo / {concelho} ({duracao:.1f}s): {e}")
            logging.error(traceback.format_exc())

    duracao_total = time.time() - inicio_geral
    sucesso = [r for r in resumo if r[2] is None]
    falhas = [r for r in resumo if r[2] is not None]

    logging.info(f"=== Fim da extração semanal do Sapo — {duracao_total / 60:.1f} min ===")
    logging.info(f"Sucesso: {len(sucesso)}/{len(resumo)}")
    total_imoveis = sum(r[1] for r in sucesso)
    logging.info(f"Total de imóveis extraídos: {total_imoveis}")
    if falhas:
        logging.warning(f"Falhas ({len(falhas)}):")
        for concelho, _, erro in falhas:
            logging.warning(f"  - {concelho}: {erro}")


if __name__ == "__main__":
    main()
