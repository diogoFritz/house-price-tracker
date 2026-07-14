"""Diagnóstico único: percebe porque é que o Idealista devolve a homepage em vez
dos resultados de Lisboa. Não faz retry nem resolve CAPTCHA — só recolhe sinais.

Uso: python scripts/diagnosticar_idealista.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import time

from house_tracker.scraping.idealista import USER_AGENT, _new_driver, _find_captcha_iframe

url = "https://www.idealista.pt/comprar-casas/lisboa/lisboa/"

driver = _new_driver(usar_proxy=True)
try:
    driver.get(url)
    time.sleep(3)

    print("URL pedido:      ", url)
    print("URL atual:       ", driver.current_url)
    print("Título:          ", driver.title)

    webdriver_flag = driver.execute_script("return navigator.webdriver")
    print("navigator.webdriver:", webdriver_flag)

    iframe = _find_captcha_iframe(driver)
    print("Iframe CAPTCHA encontrado:", iframe is not None)

    cookies = driver.get_cookies()
    datadome_cookies = [c for c in cookies if "datadome" in c["name"].lower()]
    print("Cookies datadome:", datadome_cookies)

    with open("idealista_diagnostico.html", "w", encoding="utf-8") as f:
        f.write(driver.page_source)
    print("HTML completo guardado em idealista_diagnostico.html")
finally:
    driver.quit()
