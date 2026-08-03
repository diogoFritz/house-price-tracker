"""Scraper para o Supercasa.

Tal como o Sapo, os resultados vêm em HTML renderizado no servidor (sem
proteção anti-bot) — não há API nem JavaScript a carregar dados à parte.
"""
import json
import logging
import os
import random
import re
import time
from datetime import date

import requests
from bs4 import BeautifulSoup
from tqdm import tqdm

from .classificacao import tem_usufruto

import urllib3

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "pt-PT,pt;q=0.9",
}
BASE_URL = "https://supercasa.pt/comprar-casas/{concelho}/com-apartamentos"
# Intervalo entre pedidos sucessivos, para não parecer tráfego automatizado.
REQUEST_DELAY_RANGE = (2.0, 4.0)

# "concelho.capitalize()" chega para nomes de uma palavra, mas não para os
# compostos (ex. "arruda-dos-vinhos" -> "Arruda Dos Vinhos" ficaria errado).
NOME_CONCELHO = {
    "arruda-dos-vinhos": "Arruda dos Vinhos",
    "lourinha": "Lourinhã",
    "sobral-de-monte-agraco": "Sobral de Monte Agraço",
    "torres-vedras": "Torres Vedras",
    "vila-franca-de-xira": "Vila Franca de Xira",
}


def _nome_concelho(concelho):
    return NOME_CONCELHO.get(concelho, concelho.capitalize())


# Muitos títulos do Supercasa são frases de marketing sem estrutura de morada
# nenhuma (ex. "VIVER LISBOA, SENTIR LISBOA", "T2 NOVO EM ANJOS - ESPAÇO,
# CONFORTO, TERRAÇO..."), e um simples split(",") apanha essas palavras como
# se fossem a freguesia. Em vez de tentar adivinhar sempre, valida-se o
# candidato e descarta-se (fica None) quando não parece mesmo um topónimo.
_PALAVRAS_NAO_FREGUESIA = {
    "estacionamento", "arrecadacao", "varanda", "terraco", "elevador", "garagem",
    "piscina", "luxo", "novo", "nova", "remodelacao", "remodelado", "remodelada",
    "mobilado", "mobilada", "suite", "conforto", "premium", "espaco",
    "localizacao", "vista", "rio", "total", "unico", "sentir", "viver",
    "quinta", "herdade", "vivenda", "predio", "armazem", "terreno", "palacete", "solar",
}


def _sem_acentos(txt):
    subs = str.maketrans("áàâãäéèêëíìîïóòôõöúùûüç", "aaaaaeeeeiiiiooooouuuuc")
    return txt.lower().translate(subs)


# Em concelhos com freguesias fundidas em 2013 (Lisboa, Amadora, Odivelas, ...)
# o ld+json devolve o bairro informal seguido da freguesia oficial entre
# parênteses, ex. "Alameda (São Jorge de Arroios)" — sem isto o bairro
# (não oficial) ficava a valer por freguesia.
def _normaliza_freguesia(txt):
    m = re.match(r"^.+\(([^()]+)\)\s*$", txt)
    txt = m.group(1).strip() if m else txt
    txt = re.sub(
        r"^Uni[aã]o(?:\s+das)?\s+freguesias\s+(?:de|do|da|dos|das)?\s*",
        "", txt, flags=re.IGNORECASE,
    ).strip()
    return txt


def _parece_freguesia(txt, concelho_nome=None):
    if not txt or len(txt) < 3:
        return False
    if txt.isupper():
        return False
    if re.search(r"\d", txt):
        return False
    if re.match(r"^(apartamento\b|moradia\b|casa\b)", txt, re.I):
        return False
    # "Arruda dos Vinhos" sozinho pode ser a freguesia-sede do concelho (mesmo
    # nome é comum em Portugal) — só se rejeita quando é um sufixo de algo
    # maior, ex. "Herdade em Arruda dos Vinhos" (aí o texto antes não é freguesia).
    if concelho_nome and txt.lower() != concelho_nome.lower() and txt.lower().endswith(concelho_nome.lower()):
        return False
    palavras = set(re.findall(r"[a-z]+", _sem_acentos(txt)))
    if palavras & _PALAVRAS_NAO_FREGUESIA:
        return False
    return True


def _url_pagina(concelho, page):
    base = BASE_URL.format(concelho=concelho)
    if page == 1:
        return base
    return f"{base}/pagina-{page}"


def _parse_listing(card, concelho_nome, pagina=None):
    title_a = card.select_one(".property-card__title a")
    href = title_a.get("href") if title_a else None
    id_match = re.search(r"/i(\d+)", href) if href else None
    titulo = title_a.get_text(strip=True) if title_a else None
    link = f"https://supercasa.pt{href}" if href else None

    tipologia_match = re.search(r"\bT\d+\b", titulo) if titulo else None
    tipologia = tipologia_match.group(0) if tipologia_match else None

    preco_tag = card.select_one(".property-card__price span")
    preco = None
    if preco_tag:
        preco_txt = re.sub(r"[^\d]", "", preco_tag.get_text(strip=True))
        preco = int(preco_txt) if preco_txt else None

    quartos, tamanho = None, None
    for feat in card.select(".property-card__feature"):
        texto = feat.get_text(strip=True)
        m_q = re.search(r"(\d+)\s*quartos?", texto, re.I)
        if m_q:
            quartos = int(m_q.group(1))
        m_a = re.search(r"(\d+)\s*m", texto)
        if m_a:
            tamanho = int(m_a.group(1))

    # O concelho já é conhecido (é o que foi pesquisado) — nunca se usa o que
    # vem do anúncio para o preencher, porque tanto o ld+json como o título
    # às vezes referem-se a uma freguesia ou a um concelho vizinho, não ao
    # concelho pesquisado (ex. resultados de "arruda-dos-vinhos" a incluir
    # anúncios de "Sobral de Monte Agraço" ou "furo e terreno" como se fossem
    # o concelho).
    freguesia, agencia, lat, lng = None, None, None, None
    script = card.find("script", {"type": "application/ld+json"})
    if script and script.string:
        try:
            dados = json.loads(script.string)
        except json.JSONDecodeError:
            dados = {}
        endereco = (dados.get("availableAtOrFrom") or {}).get("address") or {}
        freguesia = endereco.get("addressRegion")
        if freguesia:
            freguesia = _normaliza_freguesia(freguesia.strip())
        agencia = (dados.get("seller") or {}).get("name")
        geo = (dados.get("availableAtOrFrom") or {}).get("geo") or {}
        lat = geo.get("latitude")
        lng = geo.get("longitude")

    # Nem todos os cartões têm o bloco ld+json (ex. os cartões "featured"
    # compactos) — quando falta, a freguesia costuma vir no próprio título,
    # em dois formatos: "Apartamento T1 em Rua X, Falagueira-Venda Nova,
    # Amadora" (freguesia isolada entre vírgulas) ou só "Moradia T3 em Santo
    # Quintino, Sobral de Monte Agraço" (freguesia colada ao "em", sem vírgula
    # a separá-la). Um simples partes[-2] apanharia "Moradia T3 em Santo
    # Quintino" inteiro no segundo caso — por isso usa-se o concelho já
    # conhecido para decidir qual dos dois formatos é este.
    if not freguesia and titulo:
        partes = [p.strip() for p in titulo.split(",")]
        candidato = None
        if len(partes) >= 3:
            candidato = partes[-2]
        elif len(partes) == 2:
            ultimo = partes[-1]
            if ultimo.lower() == concelho_nome.lower():
                m = re.search(r"\b(?:em|na|no)\s+(.+)$", partes[0], re.I)
                candidato = m.group(1).strip() if m else None
            else:
                candidato = ultimo
        freguesia = candidato if _parece_freguesia(candidato, concelho_nome) else None

    # Só os cartões "premium" completos têm descrição — os compactos "featured" não.
    desc_tag = card.select_one(".property-card__description")
    descricao = desc_tag.get_text(" ", strip=True) if desc_tag else None

    return {
        "id": int(id_match.group(1)) if id_match else None,
        "pagina": pagina,
        "titulo": titulo,
        "link": link,
        "tipologia": tipologia,
        "quartos": quartos,
        "tamanho": tamanho,
        "preco": preco,
        "preco_por_metro": round(preco / tamanho, 2) if preco and tamanho else None,
        "freguesia": freguesia,
        "concelho": concelho_nome,
        "distrito": "Lisboa",
        "agencia": agencia,
        "lat": lat,
        "lng": lng,
        "descricao": descricao,
        "usufruto": tem_usufruto(f"{titulo or ''} {descricao or ''}"),
        "data_extracao": date.today().strftime("%Y%m%d"),
        "origem": "Supercasa",
    }


def _fetch_page(concelho, page):
    """Devolve a lista de imóveis de uma página, ou None se a página não existir/falhar."""
    url = _url_pagina(concelho, page)
    if page > 1:
        time.sleep(random.uniform(*REQUEST_DELAY_RANGE))
    r = requests.get(url, headers=HEADERS, timeout=30, verify=False)
    if r.status_code != 200:
        logging.error(f"[Supercasa] Erro {r.status_code} na página {page} (concelho={concelho})")
        return None

    soup = BeautifulSoup(r.text, "html.parser")
    cards = soup.find_all("article", class_="property-card")
    return [_parse_listing(card, _nome_concelho(concelho), pagina=page) for card in cards]



# O Supercasa nunca devolve uma página vazia: depois de esgotar os resultados
# reais, vai preenchendo as páginas seguintes com anúncios "premium" repetidos
# (misturados com alguns diferentes de cada vez, não sempre os mesmos) — uma
# única página só com repetidos não chega para detetar o fim. Em vez disso,
# olha-se para a taxa de anúncios novos numa janela de páginas seguidas.
JANELA_DETECAO_FIM = 5
MIN_NOVOS_NA_JANELA = 10


def fetch_all_listings(concelho="lisboa", output_dir="supercasa/json"):
    """Extrai todos os imóveis de um concelho, percorrendo páginas até a taxa de
    anúncios novos cair para perto de zero (ver JANELA_DETECAO_FIM). A lista
    final é deduplicada por id — anúncios "premium" aparecem repetidos em
    várias páginas mesmo dentro da zona de resultados reais."""
    concelho_dir = os.path.join(output_dir, concelho)
    os.makedirs(concelho_dir, exist_ok=True)

    all_listings = []
    ids_vistos = set()
    novos_por_pagina = []
    page = 1
    barra = tqdm(desc=f"Supercasa [{concelho}]", unit="página")

    while True:
        page_file = os.path.join(concelho_dir, f"pagina_{page}.json")

        if os.path.exists(page_file):
            with open(page_file, encoding="utf-8") as f:
                items = json.load(f)
            all_listings.extend(items)
            novos_por_pagina.append(sum(1 for item in items if item["id"] not in ids_vistos))
            ids_vistos.update(item["id"] for item in items)
            barra.set_postfix_str(f"página {page} em cache")
            barra.update(1)
            page += 1
            continue

        inicio = time.time()
        items = _fetch_page(concelho, page)
        duracao = time.time() - inicio
        if items is None:
            if page == 1:
                raise RuntimeError(
                    f"[Supercasa] Falha ao obter a página 1 de {concelho} — possível bloqueio do site. "
                    "Não gravado como 0 imóveis para não mascarar a falha."
                )
            tqdm.write(
                f"[Supercasa] Falha na página {page} ({duracao:.1f}s). Progresso gravado até à página {page - 1}. "
                "Chama fetch_all_listings outra vez mais tarde para retomar a partir daqui."
            )
            break
        if not items:
            if page == 1:
                raise RuntimeError(
                    f"[Supercasa] Página 1 de {concelho} devolveu 0 imóveis — possível página de bloqueio "
                    "servida com status 200. Não gravado como 0 imóveis para não mascarar a falha."
                )
            break

        novos = sum(1 for item in items if item["id"] not in ids_vistos)
        novos_por_pagina.append(novos)
        janela = novos_por_pagina[-JANELA_DETECAO_FIM:]
        if len(janela) == JANELA_DETECAO_FIM and sum(janela) < MIN_NOVOS_NA_JANELA:
            tqdm.write(
                f"[Supercasa] Últimas {JANELA_DETECAO_FIM} páginas quase só repetem anúncios já vistos "
                f"— fim real dos resultados na página {page} ({duracao:.1f}s)."
            )
            break

        with open(page_file, "w", encoding="utf-8") as f:
            json.dump(items, f, ensure_ascii=False, indent=2)

        all_listings.extend(items)
        ids_vistos.update(item["id"] for item in items)
        barra.set_postfix_str(f"{len(items)} imóveis ({novos} novos), {duracao:.1f}s")
        barra.update(1)
        page += 1

    barra.close()

    vistos, deduplicados = set(), []
    for item in all_listings:
        if item["id"] not in vistos:
            vistos.add(item["id"])
            deduplicados.append(item)

    consolidated_file = os.path.join(output_dir, f"{concelho}_{date.today().strftime('%Y%m%d')}.json")
    with open(consolidated_file, "w", encoding="utf-8") as f:
        json.dump(deduplicados, f, ensure_ascii=False, indent=2)
    logging.info(
        f"[Supercasa] Total: {len(deduplicados)} imóveis únicos "
        f"({len(all_listings) - len(deduplicados)} duplicados removidos) consolidados em {consolidated_file}"
    )

    return all_listings
