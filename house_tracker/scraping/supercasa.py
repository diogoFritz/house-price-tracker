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


def _url_pagina(concelho, page):
    base = BASE_URL.format(concelho=concelho)
    if page == 1:
        return base
    return f"{base}/pagina-{page}"


def _parse_listing(card, pagina=None):
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

    freguesia, concelho_nome, agencia, lat, lng = None, None, None, None, None
    script = card.find("script", {"type": "application/ld+json"})
    if script and script.string:
        try:
            dados = json.loads(script.string)
        except json.JSONDecodeError:
            dados = {}
        endereco = (dados.get("availableAtOrFrom") or {}).get("address") or {}
        freguesia = endereco.get("addressRegion")
        concelho_nome = endereco.get("addressLocality")
        agencia = (dados.get("seller") or {}).get("name")
        geo = (dados.get("availableAtOrFrom") or {}).get("geo") or {}
        lat = geo.get("latitude")
        lng = geo.get("longitude")

    # Nem todos os cartões têm o bloco ld+json (ex. os cartões "featured"
    # compactos) — quando falta, a freguesia e o concelho costumam vir no
    # próprio título, ex. "Apartamento T1 em Rua X, Falagueira-Venda Nova, Amadora".
    if not freguesia and titulo:
        partes = [p.strip() for p in titulo.split(",")]
        if len(partes) >= 2:
            freguesia = freguesia or partes[-2]
            concelho_nome = concelho_nome or partes[-1]

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
    return [_parse_listing(card, pagina=page) for card in cards]



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
            tqdm.write(
                f"[Supercasa] Falha na página {page} ({duracao:.1f}s). Progresso gravado até à página {page - 1}. "
                "Chama fetch_all_listings outra vez mais tarde para retomar a partir daqui."
            )
            break
        if not items:
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
