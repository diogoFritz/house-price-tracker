"""Extração do Idealista para os distritos adjacentes a Lisboa (Setúbal,
Santarém, Leiria) — o primeiro passo para estender o mapa para lá da AML.

Mesma receita do run_idealista.py (uma sessão de browser partilhada, janela
visível, retomável). Nenhum destes concelhos passa do limite de paginação do
Idealista (~1800), por isso não é preciso split por freguesia como em Lisboa.

Uso: python scripts/run_idealista_adjacentes.py
"""
import logging
import ssl
import sys
import time
import traceback
from datetime import date
from pathlib import Path

# Contexto SSL não-verificado à cabeça (a interceção TLS deste PC quebra o
# download do chromedriver pelo undetected-chromedriver). Igual à sondagem.
ssl._create_default_https_context = ssl._create_unverified_context

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import undetected_chromedriver as uc
from house_tracker.scraping.idealista import BloqueadoError, fetch_all_listings


def new_driver():
    o = uc.ChromeOptions()
    o.add_argument("--lang=pt-PT")
    return uc.Chrome(options=o, headless=False, use_subprocess=True)

# Concelhos por distrito (slugs de URL do Idealista, validados a 2026-08-07).
DISTRITOS = {
    "Setúbal": [
        "alcacer-do-sal", "alcochete", "almada", "barreiro", "grandola", "moita",
        "montijo", "palmela", "santiago-do-cacem", "seixal", "sesimbra", "setubal", "sines",
    ],
    "Santarém": [
        "abrantes", "alcanena", "almeirim", "alpiarca", "benavente", "cartaxo",
        "chamusca", "constancia", "coruche", "entroncamento", "ferreira-do-zezere",
        "golega", "macao", "ourem", "rio-maior", "salvaterra-de-magos", "santarem",
        "sardoal", "tomar", "torres-novas", "vila-nova-da-barquinha",
    ],
    "Leiria": [
        "alcobaca", "alvaiazere", "ansiao", "batalha", "bombarral", "caldas-da-rainha",
        "castanheira-de-pera", "figueiro-dos-vinhos", "leiria", "marinha-grande",
        "nazare", "obidos", "pedrogao-grande", "peniche", "pombal", "porto-de-mos",
    ],
}

LOG_DIR = Path(__file__).resolve().parent.parent / "logs"


def main():
    LOG_DIR.mkdir(exist_ok=True)
    data_hoje = date.today().strftime("%Y%m%d")
    log_file = LOG_DIR / f"run_idealista_adjacentes_{data_hoje}.log"
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(levelname)s - %(message)s",
        handlers=[logging.FileHandler(log_file, encoding="utf-8"), logging.StreamHandler()],
    )

    concelhos = [(dist, c) for dist, cs in DISTRITOS.items() for c in cs]
    logging.info(f"=== Idealista adjacentes ({data_hoje}) — {len(concelhos)} concelhos ===")
    inicio_geral = time.time()
    resumo = []

    driver = new_driver()
    try:
        for dist, concelho in concelhos:
            inicio = time.time()
            # Retenta em erros de rede (DNS/ligação) — a ligação deste PC é
            # instável; a extração é retomável, por isso repetir é barato.
            for tentativa in range(1, 4):
                try:
                    items = fetch_all_listings(concelho=concelho, driver=driver)
                    resumo.append((dist, concelho, len(items), None))
                    logging.info(f"[OK] {dist} / {concelho}: {len(items)} imóveis ({time.time()-inicio:.1f}s)")
                    break
                except BloqueadoError as e:
                    resumo.append((dist, concelho, None, str(e)))
                    logging.error(f"[BLOQUEADO] {dist} / {concelho}: {e} — a parar (retomável).")
                    driver.quit()
                    return _fim(resumo, inicio_geral)
                except Exception as e:
                    msg = str(e).splitlines()[0]
                    rede = any(x in msg for x in ("ERR_NAME_NOT_RESOLVED", "ERR_CONNECTION", "ERR_INTERNET", "timeout"))
                    if rede and tentativa < 3:
                        logging.warning(f"[rede] {dist}/{concelho} tentativa {tentativa}/3 — {msg}; espera 20s")
                        time.sleep(20)
                        try:  # a sessão pode ter morrido — recria se preciso
                            driver.current_url
                        except Exception:
                            logging.warning("driver morto, a recriar sessão")
                            try: driver.quit()
                            except Exception: pass
                            driver = new_driver()
                        continue
                    resumo.append((dist, concelho, None, msg))
                    logging.error(f"[FALHOU] {dist} / {concelho}: {msg}")
                    break
    finally:
        try: driver.quit()
        except Exception: pass

    _fim(resumo, inicio_geral)


def _fim(resumo, inicio_geral):

    sucesso = [r for r in resumo if r[3] is None]
    logging.info(f"=== Fim — {(time.time()-inicio_geral)/60:.1f} min ===")
    logging.info(f"Sucesso: {len(sucesso)}/{len(resumo)} · total imóveis: {sum(r[2] for r in sucesso)}")
    for dist, concelho, _, erro in resumo:
        if erro:
            logging.warning(f"  falhou {dist}/{concelho}: {erro}")


if __name__ == "__main__":
    main()
