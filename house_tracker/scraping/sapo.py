import json
import logging
import math
import os
import random
import re
import time
from datetime import date
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

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
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "pt-PT,pt;q=0.9,en;q=0.8",
}
BASE_URL = "https://casa.sapo.pt/comprar-apartamentos/{concelho}/?pn={page}"
# Intervalo entre pedidos sucessivos, para não parecer tráfego automatizado
# e evitar o bloqueio por limite de pedidos (HTTP 429) do casa.sapo.pt.
REQUEST_DELAY_RANGE = (2.0, 4.0)

# O casa.sapo.pt bloqueia com 429 depois de ~5 páginas seguidas — em vez de
# desistir, espera-se e repete a mesma página automaticamente.
RATE_LIMIT_WAIT_S = 90
RATE_LIMIT_MAX_TENTATIVAS = 2

# Sessão partilhada: mantém cookies entre pedidos, tal como um browser real.
_session = requests.Session()
_session.headers.update(HEADERS)


def _get(url, delay=True):
    if delay:
        time.sleep(random.uniform(*REQUEST_DELAY_RANGE))
    return _session.get(url, verify=False, timeout=15)


def _fetch_page(concelho, page, delay=True):
    url = BASE_URL.format(concelho=concelho, page=page)
    resp = _get(url, delay=delay)
    if resp.status_code != 200:
        logging.error(f"Erro ao aceder à página {page} ({url}) - status {resp.status_code}")
        return None
    return BeautifulSoup(resp.text, "html.parser")


def _resolve_link(link, concelho):
    if not link:
        return None
    if link.startswith("https://gespub.casa.sapo.pt"):
        qs = parse_qs(urlparse(link).query)
        if "l" in qs:
            return unquote(qs["l"][0])
        return link
    if link.startswith("/"):
        return f"https://casa.sapo.pt{link}"
    return link


def _parse_listing(prop, pagina, idx, concelho=None):
    """Extrai os campos de um único anúncio (div.property-info-content)."""
    a_tag = prop.find("a", class_="property-info")
    link = _resolve_link(a_tag["href"], concelho) if a_tag else None
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
    preco_box = prop.find("div", class_="property-price")
    if preco_box:
        preco_old_tag = preco_box.find("div", class_="property-price-old")
        if preco_old_tag:
            preco_antigo = re.sub(r"[^\d]", "", preco_old_tag.get_text(strip=True))
            preco_antigo = int(preco_antigo) if preco_antigo else None

        preco_tag = preco_box.find("div", class_="property-price-value")
        if preco_tag:
            strong = preco_tag.find("strong")
            span = preco_tag.find("span")
            if strong:
                preco_txt = strong.get_text(strip=True)
            elif span:
                preco_txt = span.get_text(strip=True)
            else:
                preco_txt = preco_tag.get_text(" ", strip=True)

            match = re.search(r"(\d[\d.\s]*)\s*€", preco_txt)
            if match:
                preco_clean = re.sub(r"[^\d]", "", match.group(1))
                preco = int(preco_clean) if preco_clean else None

        desconto_tag = preco_box.find("div", class_="property-price-discount")
        if desconto_tag:
            desconto = desconto_tag.get_text(strip=True)

    agencia_tag = prop.find("div", class_="property-owner-logo")
    agencia = None
    if agencia_tag:
        agencia_link = agencia_tag.find("a")
        if agencia_link and agencia_link.get("title"):
            agencia = agencia_link["title"].replace("Veja todos os imóveis do anunciante ", "").strip()

    preco_por_metro = round(preco / tamanho, 2) if preco and tamanho else None

    desc_tag = prop.find("div", class_="property-description")
    descricao = desc_tag.get_text(" ", strip=True) if desc_tag else None

    # Contagem best-effort: só as fotos do carrossel de preview do cartão, não
    # o total real de fotos do anúncio (esse só está na página de detalhe).
    # As fotos ficam fora de "property-info-content", no <div class="property"> pai.
    num_fotos = len(prop.parent.find_all("picture", class_="property-photos")) if prop.parent else None

    return {
        "pagina": pagina,
        "id": idx,
        "titulo": titulo,
        "link": link,
        "tipologia": tipologia,
        "estado": estado,
        "tamanho": tamanho,
        "preco": preco,
        "preco_por_metro": preco_por_metro,
        "preco_antigo": preco_antigo,
        "desconto": desconto,
        "localizacao": localizacao,
        "agencia": agencia,
        "descricao": descricao,
        "num_fotos": num_fotos,
        "usufruto": tem_usufruto(f"{titulo or ''} {descricao or ''}"),
        "data_publicacao": data_pub,
        "data_extracao": date.today().strftime("%Y%m%d"),
        "origem": "Sapo",
    }


def get_sapo_url(concelho="amadora", min_preco=100000, max_preco=400000, min_tamanho=30,
                  max_tamanho=150, page=None, estado=None, certificado_energetico=None):
    """Constrói um URL de pesquisa com filtros, ex.:
    https://casa.sapo.pt/comprar-apartamentos/usado/lisboa/?lp=100000&gp=300000&lau=30&gau=150&ecv=25&pn=1
    """
    url = "https://casa.sapo.pt/comprar-apartamentos/"
    if estado:
        url += f"{estado}/"
    url += f"{concelho}/?pn={page if page else 1}"

    if min_preco is not None:
        url += f"&lp={min_preco}"
    if max_preco is not None:
        url += f"&gp={max_preco}"
    if min_tamanho is not None:
        url += f"&lau={min_tamanho}"
    if max_tamanho is not None:
        url += f"&gau={max_tamanho}"
    if estado is not None:
        url += f"&estado={estado}"
    if certificado_energetico is not None:
        url += f"&ecv={certificado_energetico}"

    logging.info(f"URL: {url}")
    return url


def download_sapo_page(url, filename):
    """Faz download de uma página e guarda em HTML local."""
    resp = _get(url)
    if resp.status_code != 200:
        logging.error(f"Erro {resp.status_code} ao aceder {url}")
        return None

    dirpath = os.path.dirname(filename)
    if dirpath:
        os.makedirs(dirpath, exist_ok=True)
    with open(filename, "w", encoding="utf-8") as f:
        f.write(resp.text)
    logging.info(f"[DOWNLOAD] Página salva em {filename}")
    return filename


def download_sapo_all(concelho="amadora", pasta="sapo/html/", forcar_novo=False):
    """Faz download de todas as páginas de resultados de um concelho no Sapo Casa.
    Para automaticamente quando encontra uma página sem resultados.

    Por omissão é retomável (tal como fetch_all_listings do Imovirtual): se uma
    página já tiver sido descarregada, é reaproveitada em vez de repedida. Usa
    forcar_novo=True para limpar tudo e recomeçar do zero.
    """
    concelho_path = os.path.join(pasta, concelho)
    os.makedirs(concelho_path, exist_ok=True)

    if forcar_novo:
        for f in os.listdir(concelho_path):
            if f.endswith(".html"):
                os.remove(os.path.join(concelho_path, f))

    page = 1
    total_downloaded = 0
    barra = tqdm(desc=f"Sapo [{concelho}]", unit="página")
    while True:
        filename = os.path.join(pasta, concelho, f"pagina{page}.html")

        if os.path.exists(filename):
            barra.set_postfix_str(f"página {page} em cache")
            total_downloaded += 1
            barra.update(1)
            page += 1
            continue

        url = f"https://casa.sapo.pt/comprar-apartamentos/{concelho}/?pn={page}"
        for tentativa in range(1, RATE_LIMIT_MAX_TENTATIVAS + 1):
            inicio = time.time()
            resp = _get(url)
            duracao = time.time() - inicio
            if resp.status_code != 429:
                break
            tqdm.write(
                f"429 (rate limit) na página {page}, tentativa {tentativa}/{RATE_LIMIT_MAX_TENTATIVAS} — "
                f"a aguardar {RATE_LIMIT_WAIT_S}s antes de repetir."
            )
            time.sleep(RATE_LIMIT_WAIT_S)

        if resp.status_code != 200:
            tqdm.write(f"Erro {resp.status_code} ao aceder {url} ({duracao:.1f}s)")
            break

        soup = BeautifulSoup(resp.text, "html.parser")
        properties = soup.find_all("div", class_="property-info-content")
        if not properties:
            barra.set_postfix_str(f"página {page} sem resultados, a parar ({duracao:.1f}s)")
            break

        with open(filename, "w", encoding="utf-8") as f:
            f.write(resp.text)

        barra.set_postfix_str(f"página {page}: {len(properties)} imóveis, {duracao:.1f}s")
        barra.update(1)
        total_downloaded += 1
        page += 1

    barra.close()
    logging.info(f"[RESUMO] Total de páginas descarregadas: {total_downloaded}")
    return total_downloaded


def parse_sapo_page(filepath, pagina=1, concelho=None):
    """Extrai os imóveis de uma página HTML previamente descarregada."""
    with open(filepath, "r", encoding="utf-8") as f:
        html = f.read()

    soup = BeautifulSoup(html, "html.parser")
    properties = soup.find_all("div", class_="property-info-content")
    logging.info(f"[DEBUG] {filepath}: {len(properties)} resultados detetados")

    return [
        _parse_listing(prop, pagina, idx, concelho)
        for idx, prop in enumerate(properties, start=1)
    ]


def parse_sapo_all(concelho="amadora", pasta_base="sapo"):
    """Extrai todas as páginas HTML descarregadas de um concelho e grava um único JSON."""
    pasta_html = os.path.join(pasta_base, "html", concelho)
    pasta_json = os.path.join(pasta_base, "json")
    os.makedirs(pasta_json, exist_ok=True)

    ficheiros = sorted(
        [f for f in os.listdir(pasta_html) if f.startswith("pagina") and f.endswith(".html")],
        key=lambda x: int(re.search(r"(\d+)", x).group(1)),
    )

    propriedades_lista = []
    for f in ficheiros:
        pagina = int(re.search(r"(\d+)", f).group(1))
        propriedades = parse_sapo_page(os.path.join(pasta_html, f), pagina=pagina, concelho=concelho)
        propriedades_lista.extend(propriedades)
        logging.info(f"[INFO] {len(propriedades)} imóveis extraídos de {f}")

    data_extracao = date.today().strftime("%Y%m%d")
    json_file = os.path.join(pasta_json, f"{concelho}_{data_extracao}.json")
    with open(json_file, "w", encoding="utf-8") as f:
        json.dump(propriedades_lista, f, ensure_ascii=False, indent=2)

    logging.info(
        f"[RESUMO] {len(propriedades_lista)} imóveis extraídos de {len(ficheiros)} páginas e salvos em {json_file}"
    )
    return propriedades_lista


def download_sapo_property(url, filename="imovel_full.html"):
    """Usa o Selenium para descarregar a página completa (renderizada) de um imóvel."""
    from selenium import webdriver
    from selenium.webdriver.chrome.options import Options
    from selenium.webdriver.common.by import By
    from selenium.webdriver.support import expected_conditions as EC
    from selenium.webdriver.support.ui import WebDriverWait

    options = Options()
    options.add_argument("--headless")
    options.add_argument("--disable-gpu")
    options.add_argument("--no-sandbox")
    options.add_argument("user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64)")

    driver = webdriver.Chrome(options=options)
    try:
        driver.get(url)
        WebDriverWait(driver, 30).until(
            EC.presence_of_element_located((By.CSS_SELECTOR, ".detail-section.detail-title"))
        )
        html = driver.page_source
        with open(filename, "w", encoding="utf-8") as f:
            f.write(html)
    finally:
        driver.quit()

    return filename


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


def scrape_sapo_site(concelho="amadora", output_dir="sapo/json"):
    """Percorre todas as páginas de um concelho (pedido em direto) e grava o resultado em JSON."""
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
