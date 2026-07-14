"""Testa a extração em lote do Idealista para um concelho: percorre várias
páginas, salta as que falham (com justificação), e mostra um resumo no final.

Uso: python scripts\\testar_idealista_lote.py [concelho] [max_paginas]
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from house_tracker.scraping.idealista import fetch_all_pages

concelho = sys.argv[1] if len(sys.argv) > 1 else "lisboa"
max_paginas = int(sys.argv[2]) if len(sys.argv) > 2 else 20

resultado = fetch_all_pages(concelho=concelho, max_paginas=max_paginas)

print()
print(f"Sucesso: {len(resultado['sucesso'])}")
print(f"Falhas por CAPTCHA: {len(resultado['falha_captcha'])}")
print(f"Falhas por outro motivo: {len(resultado['falha_outra'])}")
