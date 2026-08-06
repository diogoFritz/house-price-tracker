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


# "Situações especiais" — categorias de anúncios que fogem à venda normal com
# desocupação imediata, cada uma útil para uma estratégia de compra diferente.
# Detetadas por palavras-chave na descrição/título (só fontes com texto livre).
_CATEGORIAS = {
    # Vendedor idoso que mantém o direito de habitar (ver _PADRAO_USUFRUTO).
    "usufruto": _PADRAO_USUFRUTO,
    # Imóvel arrendado / vendido como investimento com inquilino — não dá para
    # habitar já, mas rende desde o primeiro dia.
    "arrendado": re.compile(
        r"arrendad|inquilin|com\s+contrato\s+de\s+arrendamento|rentabilidade|"
        r"rendimento\s+garantid|j[aá]\s+arrendad",
        re.IGNORECASE,
    ),
    # Precisa de obras — preço mais baixo, exige investimento em reabilitação.
    "obras": re.compile(
        r"para\s+recuperar|para\s+restaurar|para\s+renovar|necessita\s+de\s+obras|"
        r"a\s+necessitar\s+de\s+obras|para\s+remodelar|para\s+reabilitar|"
        r"em\s+ru[ií]na|para\s+demolir|devoluto",
        re.IGNORECASE,
    ),
    # Herança/partilha — vendedores muitas vezes com pressa e preço negociável.
    "heranca": re.compile(r"heran[çc]a|partilha[s]?|cabe[çc]a\s+de\s+casal", re.IGNORECASE),
    # Permuta — o vendedor aceita troca por outro imóvel.
    "permuta": re.compile(r"permuta|aceita\s+troca", re.IGNORECASE),
    # Vendedor motivado / urgência — sinais de flexibilidade no preço.
    "urgente": re.compile(
        r"urge|urgente|venda\s+urgente|preço\s+negoci[aá]vel|baixa\s+de\s+pre[çc]o|"
        r"abaixo\s+do\s+valor\s+de\s+mercado|oportunidade\s+[uú]nica",
        re.IGNORECASE,
    ),
}


def categorias(texto):
    """Devolve a lista de "situações especiais" detetadas no texto
    (ex. ["obras", "heranca"]). Lista vazia se nenhuma; None se não houver
    texto para analisar."""
    if not texto:
        return None
    return [nome for nome, padrao in _CATEGORIAS.items() if padrao.search(texto)]
