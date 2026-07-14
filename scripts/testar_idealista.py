"""Teste rápido: tenta ir buscar uma página de resultados do Idealista, resolvendo
o CAPTCHA via 2Captcha se aparecer."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from house_tracker.scraping.idealista import CaptchaFalhouError, fetch_page

url = "https://www.idealista.pt/comprar-casas/lisboa/lisboa/"

try:
    html = fetch_page(url)
except CaptchaFalhouError as e:
    print(f"FALHOU: {e}")
else:
    print(f"SUCESSO: página obtida, {len(html)} caracteres.")
    with open("idealista_teste.html", "w", encoding="utf-8") as f:
        f.write(html)
    print("Guardado em idealista_teste.html para inspecionar.")
