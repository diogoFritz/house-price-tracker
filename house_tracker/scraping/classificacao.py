"""Classificação de anúncios a partir de texto livre (descrição/título).

Usado pelas fontes que expõem uma descrição completa (Sapo, Imovirtual,
Supercasa) — as que só têm API estruturada sem texto livre (Remax, Century21,
ERA) não conseguem ser classificadas desta forma.
"""
import re

# Venda com usufruto/nua-propriedade: o vendedor (tipicamente idoso) mantém o
# direito de habitar o imóvel, e o comprador adquire a "nua propriedade" —
# só fica com a propriedade plena mais tarde. Distinto de uma venda normal
# com desocupação imediata.
_PADRAO_USUFRUTO = re.compile(
    r"usufrut|nua[\s-]propriedade|direito de habita[çc][aã]o|reserva de usufruto",
    re.IGNORECASE,
)


def tem_usufruto(texto):
    """Devolve True se o texto (descrição/título) indicar uma venda com
    usufruto/nua-propriedade reservada ao vendedor. None se não houver texto
    para analisar (fonte sem descrição disponível)."""
    if not texto:
        return None
    return bool(_PADRAO_USUFRUTO.search(texto))
