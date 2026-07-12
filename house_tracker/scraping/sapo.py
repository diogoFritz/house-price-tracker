import json
import logging
import math
import random
import re
import time
from datetime import date
from pathlib import Path

import requests
from bs4 import BeautifulSoup

import urllib3

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "pt-PT,pt;q=0.9,en;q=0.8",
}
BASE_URL = "https://casa.sapo.pt/comprar-apartamentos/{concelho}/?pn={page}"
# Intervalo entre pedidos sucessivos, para não parecer tráfego automatizado
# e evitar o bloqueio por limite de pedidos (HTTP 429) do casa.sapo.pt.
REQUEST_DELAY_RANGE = (2.0, 4.0)

# Sessão partilhada: mantém cookies entre pedidos, tal como um browser real.
_session = requests.Session()
_session.headers.update(HEADERS)


def _fetch_page(concelho, page, delay=True):
    url = BASE_URL.format(concelho=concelho, page=page)
    if delay:
        time.sleep(random.uniform(*REQUEST_DELAY_RANGE))
    resp = _session.get(url, verify=False, timeout=15)
    if resp.status_code != 200:
        logging.error(f"Erro ao aceder à página {page} ({url}) - status {resp.status_code}")
        return None
    return BeautifulSoup(resp.text, "html.parser")


def _parse_listing(prop, page, idx, concelho=None):
    """Extrai os campos de um único anúncio (div.property-info-content)."""
    a_tag = prop.find("a", class_="property-info")
    link = a_tag["href"] if a_tag else None
    if link and link.startswith("/"):
        link = f"https://casa.sapo.pt{link}"
    titulo = a_tag["title"] if a_tag else None

    features_tag = prop.find("div", class_="property-features-text")
    estado, tamanho = None, None
    if features_tag:
        partes = [p.strip() for p in features_tag.get_text(strip=True).split("·")]
        if len(partes) >= 1:
            estado = partes[0]
        if len(partes) >= 2 and "m²" in partes[1]:
            tamanho = re.sub(r"[^\d]", "", partes[1])
            tamanho = int(tamanho) if tamanho else None

    tipologia = None
    if titulo:
        m = re.search(r"T\d+", titulo)
        tipologia = m.group(0) if m else None

    local_tag = prop.find("div", class_="property-location")
    localizacao = local_tag.get_text(strip=True) if local_tag else None

    data_tag = prop.find("div", class_="property-date")
    data_pub = data_tag.get_text(strip=True) if data_tag else None

    preco, preco_antigo, desconto = None, None, None

    preco_old_tag = prop.find("div", class_="property-price-old")
    if preco_old_tag:
        preco_antigo = re.sub(r"[^\d]", "", preco_old_tag.get_text(strip=True))
        preco_antigo = int(preco_antigo) if preco_antigo else None

    preco_tag = prop.find("div", class_="property-price-value")
    if preco_tag:
        preco = re.sub(r"[^\d]", "", preco_tag.get_text(strip=True))
        preco = int(preco) if preco else None

    desconto_tag = prop.find("div", class_="property-price-discount")
    if desconto_tag:
        desconto = desconto_tag.get_text(strip=True)

    preco_por_metro = round(preco / tamanho, 2) if preco and tamanho else None

    return {
        "pagina": page,
        "id": idx,
        "titulo": titulo,
        "link": link,
        "tipologia": tipologia,
        "estado": estado,
        "tamanho": tamanho,
        "preco": preco,
        "preco_antigo": preco_antigo,
        "desconto": desconto,
        "preco_por_metro": preco_por_metro,
        "localizacao": localizacao,
        "data_publicacao": data_pub,
    }


def _page1_stats(concelho):
    """Pede a página 1 uma única vez e devolve (soup, total_paginas, total_casas, resultados_por_pagina)."""
    soup = _fetch_page(concelho, 1)
    if soup is None:
        return None, None, None, None

    total_casas = None
    titulo_pesquisa = soup.find("div", class_="list-title")
    if titulo_pesquisa:
        match = re.search(r"(\d+)", titulo_pesquisa.get_text(strip=True))
        if match:
            total_casas = int(match.group(1))

    properties = soup.find_all("div", class_="property-info-content")
    resultados_por_pagina = len(properties)

    total_paginas = None
    pager = soup.find("ul", class_="pager")
    if pager:
        paginas = [int(a.get_text()) for a in pager.find_all("a") if a.get_text().isdigit()]
        if paginas:
            total_paginas = max(paginas)
    elif total_casas and resultados_por_pagina:
        total_paginas = math.ceil(total_casas / resultados_por_pagina)

    logging.info(f"Total casas: {total_casas}")
    logging.info(f"Resultados por página: {resultados_por_pagina}")
    logging.info(f"Total páginas: {total_paginas}")

    return soup, total_paginas, total_casas, resultados_por_pagina


def get_sapo_results(concelho="amadora"):
    """Calcula o total de casas, resultados por página e total de páginas."""
    _, total_paginas, total_casas, resultados_por_pagina = _page1_stats(concelho)
    return total_paginas, total_casas, resultados_por_pagina


def scrape_sapo_site(concelho="amadora", output_dir="data/json"):
    """Percorre todas as páginas de um concelho e grava o resultado em JSON."""
    soup, total_paginas, _, _ = _page1_stats(concelho)
    if not total_paginas:
        logging.error(f"Não foi possível determinar o total de páginas para {concelho}")
        return []

    propriedades_lista = []
    for page in range(1, total_paginas + 1):
        # A página 1 já foi obtida em _page1_stats; reutiliza-a em vez de repetir o pedido.
        page_soup = soup if page == 1 else _fetch_page(concelho, page)
        if page_soup is None:
            continue

        properties = page_soup.find_all("div", class_="property-info-content")
        logging.info(f"Página {page}: {len(properties)} resultados detetados")

        for idx, prop in enumerate(properties, start=1):
            propriedades_lista.append(_parse_listing(prop, page, idx, concelho))

    date_format = date.today().strftime("%Y_%m_%d")
    Path(output_dir).mkdir(parents=True, exist_ok=True)
    file_name = Path(output_dir) / f"propriedades_sapo_{concelho}_{date_format}.json"
    with open(file_name, "w", encoding="utf-8") as json_file:
        json.dump(propriedades_lista, json_file, ensure_ascii=False, indent=2)
    logging.info(f"{file_name} salvo com sucesso")

    return propriedades_lista


def save_page_html(concelho="lisboa", output_dir="data/raw_html"):
    """Grava o HTML bruto da primeira página de um concelho, para depuração offline."""
    soup = _fetch_page(concelho, 1)
    if soup is None:
        return None

    Path(output_dir).mkdir(parents=True, exist_ok=True)
    date_format = date.today().strftime("%Y_%m_%d")
    file_name = Path(output_dir) / f"sapo_{concelho}_{date_format}.html"
    with open(file_name, "w", encoding="utf-8") as f:
        f.write(soup.prettify())
    logging.info(f"Página salva como {file_name}")
    return file_name


def parse_saved_html(filepath, output_dir="data/json"):
    """Lê um HTML previamente gravado e extrai as propriedades para JSON."""
    filepath = Path(filepath)
    concelho = filepath.stem.split('_')[1]

    with open(filepath, "r", encoding="utf-8") as f:
        content = f.read()

    soup = BeautifulSoup(content, "html.parser")
    properties = soup.find_all("div", class_="property-info-content")
    logging.info(f"Encontradas {len(properties)} resultados em {concelho}")

    propriedades_lista = [
        _parse_listing(prop, page=1, idx=idx, concelho=concelho)
        for idx, prop in enumerate(properties, start=1)
    ]

    date_format = date.today().strftime("%Y_%m_%d")
    Path(output_dir).mkdir(parents=True, exist_ok=True)
    file_name = Path(output_dir) / f"propriedades_sapo_{date_format}.json"
    with open(file_name, "w", encoding="utf-8") as json_file:
        json.dump(propriedades_lista, json_file, ensure_ascii=False, indent=2)
    logging.info(f"{file_name} salvo com sucesso")

    return propriedades_lista
