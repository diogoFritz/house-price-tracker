"""Extração semanal automática: corre todas as fontes (exceto Sapo) para todos
os 16 concelhos do distrito de Lisboa.

Pensado para correr sem supervisão (ex. via Task Scheduler do Windows) — uma
falha num concelho/fonte não interrompe os restantes, fica tudo registado num
log com data.

Uso: python scripts/run_semanal.py
"""
import logging
import sys
import time
import traceback
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from house_tracker.scraping.century21 import fetch_all_listings as fetch_century21
from house_tracker.scraping.era import fetch_all_listings as fetch_era
from house_tracker.scraping.imovirtual import fetch_all_listings as fetch_imovirtual
from house_tracker.scraping.remax import fetch_all_listings as fetch_remax
from house_tracker.scraping.supercasa import fetch_all_listings as fetch_supercasa

CONCELHOS = [
    "lisboa", "amadora", "loures", "odivelas", "oeiras", "sintra",
    "alenquer", "arruda-dos-vinhos", "azambuja", "cadaval", "cascais",
    "lourinha", "mafra", "sobral-de-monte-agraco", "torres-vedras",
    "vila-franca-de-xira",
]

# Imovirtual precisa do distrito explícito (todos estes concelhos são do distrito de Lisboa).
FONTES = {
    "Imovirtual": lambda concelho: fetch_imovirtual(concelho=concelho, distrito="lisboa"),
    "Remax": lambda concelho: fetch_remax(concelho=concelho),
    "Century21": lambda concelho: fetch_century21(concelho=concelho),
    "ERA": lambda concelho: fetch_era(concelho=concelho),
    "Supercasa": lambda concelho: fetch_supercasa(concelho=concelho),
}

LOG_DIR = Path(__file__).resolve().parent.parent / "logs"


def main():
    LOG_DIR.mkdir(exist_ok=True)
    data_hoje = date.today().strftime("%Y%m%d")
    log_file = LOG_DIR / f"run_semanal_{data_hoje}.log"

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(levelname)s - %(message)s",
        handlers=[logging.FileHandler(log_file, encoding="utf-8"), logging.StreamHandler()],
    )

    logging.info(f"=== Início da extração semanal ({data_hoje}) ===")
    inicio_geral = time.time()
    resumo = []

    for nome_fonte, fetch_fn in FONTES.items():
        for concelho in CONCELHOS:
            inicio = time.time()
            try:
                items = fetch_fn(concelho)
                duracao = time.time() - inicio
                resumo.append((nome_fonte, concelho, len(items), None))
                logging.info(f"[OK] {nome_fonte} / {concelho}: {len(items)} imóveis ({duracao:.1f}s)")
            except Exception as e:
                duracao = time.time() - inicio
                resumo.append((nome_fonte, concelho, None, str(e)))
                logging.error(f"[FALHOU] {nome_fonte} / {concelho} ({duracao:.1f}s): {e}")
                logging.error(traceback.format_exc())

    duracao_total = time.time() - inicio_geral
    sucesso = [r for r in resumo if r[3] is None]
    falhas = [r for r in resumo if r[3] is not None]

    logging.info(f"=== Fim da extração semanal — {duracao_total / 60:.1f} min ===")
    logging.info(f"Sucesso: {len(sucesso)}/{len(resumo)}")
    total_imoveis = sum(r[2] for r in sucesso)
    logging.info(f"Total de imóveis extraídos: {total_imoveis}")
    if falhas:
        logging.warning(f"Falhas ({len(falhas)}):")
        for nome_fonte, concelho, _, erro in falhas:
            logging.warning(f"  - {nome_fonte} / {concelho}: {erro}")


if __name__ == "__main__":
    main()
