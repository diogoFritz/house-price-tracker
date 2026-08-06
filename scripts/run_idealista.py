"""Extração do Idealista para todos os 16 concelhos do distrito de Lisboa.

Separada do run_semanal.py porque precisa de um browser com janela visível
(ver house_tracker/scraping/idealista.py) — só corre num PC com sessão
gráfica, não no GitHub Actions.

Uma única sessão de browser é partilhada por todos os concelhos: cada sessão
nova é uma nova avaliação de risco do DataDome, por isso quantas menos, melhor.

Uso: python scripts/run_idealista.py
"""
import logging
import sys
import time
import traceback
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from house_tracker.scraping.idealista import BloqueadoError, fetch_all_listings, new_driver

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
    log_file = LOG_DIR / f"run_idealista_{data_hoje}.log"

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(levelname)s - %(message)s",
        handlers=[logging.FileHandler(log_file, encoding="utf-8"), logging.StreamHandler()],
    )

    logging.info(f"=== Início da extração do Idealista ({data_hoje}) ===")
    inicio_geral = time.time()
    resumo = []

    driver = new_driver()
    try:
        for concelho in CONCELHOS:
            inicio = time.time()
            try:
                items = fetch_all_listings(concelho=concelho, driver=driver)
                duracao = time.time() - inicio
                resumo.append((concelho, len(items), None))
                logging.info(f"[OK] Idealista / {concelho}: {len(items)} imóveis ({duracao:.1f}s)")
            except BloqueadoError as e:
                duracao = time.time() - inicio
                resumo.append((concelho, None, str(e)))
                logging.error(f"[BLOQUEADO] Idealista / {concelho} ({duracao:.1f}s): {e}")
                logging.error(
                    "Sessão marcada pelo DataDome — o melhor é parar já e tentar "
                    "outra vez mais tarde (a extração é retomável)."
                )
                break
            except Exception as e:
                duracao = time.time() - inicio
                resumo.append((concelho, None, str(e)))
                logging.error(f"[FALHOU] Idealista / {concelho} ({duracao:.1f}s): {e}")
                logging.error(traceback.format_exc())
    finally:
        driver.quit()

    duracao_total = time.time() - inicio_geral
    sucesso = [r for r in resumo if r[2] is None]
    falhas = [r for r in resumo if r[2] is not None]

    logging.info(f"=== Fim da extração do Idealista — {duracao_total / 60:.1f} min ===")
    logging.info(f"Sucesso: {len(sucesso)}/{len(resumo)}")
    total_imoveis = sum(r[1] for r in sucesso)
    logging.info(f"Total de imóveis extraídos: {total_imoveis}")
    if falhas:
        logging.warning(f"Falhas ({len(falhas)}):")
        for concelho, _, erro in falhas:
            logging.warning(f"  - {concelho}: {erro}")


if __name__ == "__main__":
    main()
