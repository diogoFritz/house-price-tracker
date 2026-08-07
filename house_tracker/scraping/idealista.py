"""Scraper para o Idealista.

O Idealista usa DataDome (proteção anti-bot), que bloqueia pedidos HTTP simples
(403 imediato). No entanto, uma única sessão persistente de undetected-chromedriver
com janela visível (headless é detetado) passa sem desafio e aguenta paginação
contínua — confirmado em agosto de 2026. A receita que funciona:

  1. Um só browser para toda a extração (nunca um browser novo por página —
     cada sessão nova é uma nova avaliação de risco do DataDome).
  2. Janela visível (headless=False) e sem proxy.
  3. Seguir o href do botão "seguinte" da paginação (formato "/pagina-N", sem
     ".htm" — o formato antigo "pagina-N.htm" devolve uma página vazia).
  4. Esperas aleatórias de alguns segundos entre páginas.

Nota: por precisar de janela visível, só corre num PC com sessão gráfica —
não funciona no GitHub Actions. A versão anterior deste módulo (resolução de
CAPTCHA via CapSolver/2Captcha + proxies) está no histórico git, caso o
DataDome volte a apertar.
"""
import json
import logging
import os
import random
import re
import ssl
import time
from datetime import date

import undetected_chromedriver as uc
from bs4 import BeautifulSoup
from tqdm import tqdm

from .classificacao import tem_usufruto

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

BASE_URL = "https://www.idealista.pt/comprar-casas/{path}/com-apartamentos/"

# Intervalo entre páginas, para não parecer tráfego automatizado.
REQUEST_DELAY_RANGE = (3.0, 5.5)

# O Idealista corta a paginação ao fim de ~60 páginas (~1800 resultados) por
# pesquisa. Lisboa tem ~9500 apartamentos, muito acima disso — uma pesquisa
# única só apanharia ~1800. Divide-se por freguesia (cada uma <900, bem abaixo
# do limite) para capturar tudo. Os restantes concelhos cabem numa pesquisa só.
LISBOA_FREGUESIAS = [
    "ajuda", "alcantara", "alvalade", "areeiro", "arroios", "avenidas-novas",
    "beato", "belem", "benfica", "campo-de-ourique", "campolide", "carnide",
    "estrela", "lumiar", "marvila", "misericordia", "olivais", "parque-das-nacoes",
    "penha-de-franca", "santa-clara", "santa-maria-maior", "santo-antonio",
    "sao-domingos-de-benfica", "sao-vicente",
]


def _segmentos(concelho):
    """Devolve [(id_segmento, path_url)] das pesquisas a fazer para um concelho.
    Um segmento por concelho, exceto Lisboa (um por freguesia)."""
    if concelho == "lisboa":
        return [(fr, f"lisboa/{fr}") for fr in LISBOA_FREGUESIAS]
    return [(concelho, concelho)]

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


def _chrome_major_version():
    """Deteta a versão principal do Chrome instalado (via registo do Windows), para
    o undetected-chromedriver descarregar o chromedriver certo — sem isto, tenta
    sempre a versão mais recente, que pode não corresponder ao Chrome instalado
    (ex.: chromedriver 151 vs Chrome 150) e falha a criar sessão."""
    import subprocess
    import winreg

    try:
        with winreg.OpenKey(
            winreg.HKEY_LOCAL_MACHINE,
            r"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\chrome.exe",
        ) as key:
            chrome_path = winreg.QueryValue(key, None)
        saida = subprocess.check_output(
            ["powershell", "-NoProfile", "-Command",
             f"(Get-Item '{chrome_path}').VersionInfo.ProductVersion"],
            text=True,
        )
        match = re.search(r"(\d+)\.", saida)
        return int(match.group(1)) if match else None
    except (OSError, subprocess.SubprocessError):
        return None


def new_driver():
    """Cria a sessão de browser partilhada por toda a extração.

    headless=False é deliberado: o DataDome deteta o modo headless e bloqueia —
    com janela visível a sessão passa sem desafio. O contorno de SSL é para o
    próprio undetected-chromedriver conseguir descarregar o chromedriver neste
    PC (interceção TLS local), não afeta os pedidos ao site.
    """
    options = uc.ChromeOptions()
    options.add_argument("--lang=pt-PT")

    original_context = ssl._create_default_https_context
    ssl._create_default_https_context = ssl._create_unverified_context
    try:
        return uc.Chrome(
            options=options, headless=False, use_subprocess=True,
            version_main=_chrome_major_version(),
        )
    finally:
        ssl._create_default_https_context = original_context


def _parse_listing(article, concelho_nome, pagina=None):
    link_a = article.select_one("a.item-link")
    titulo = (link_a.get("title") or link_a.get_text(strip=True)) if link_a else None
    href = link_a.get("href") if link_a else None
    link = f"https://www.idealista.pt{href}" if href else None

    id_txt = article.get("data-element-id")
    id_ = int(id_txt) if id_txt and id_txt.isdigit() else None

    preco = None
    preco_tag = article.select_one(".item-price")
    if preco_tag:
        digitos = re.sub(r"[^\d]", "", preco_tag.get_text(strip=True))
        preco = int(digitos) if digitos else None

    # Descida de preço: o Idealista mostra o preço anterior (riscado) e a %
    # de desconto quando o vendedor baixou o preço — sinal forte de vendedor
    # motivado. Ausente na maioria dos anúncios (fica None).
    preco_antigo, desconto_pct = None, None
    antigo_tag = article.select_one(".pricedown_price")
    if antigo_tag:
        digitos = re.sub(r"[^\d]", "", antigo_tag.get_text(strip=True))
        preco_antigo = int(digitos) if digitos else None
        if preco and preco_antigo and preco_antigo > 0:
            desconto_pct = round(100 * (preco - preco_antigo) / preco_antigo, 1)

    tipologia, tamanho = None, None
    for det in article.select(".item-detail"):
        texto = det.get_text(strip=True)
        m_t = re.match(r"^T(\d+)", texto)
        if m_t:
            tipologia = f"T{m_t.group(1)}"
        m_a = re.search(r"(\d+)\s*m²", texto)
        if m_a:
            tamanho = int(m_a.group(1))
    quartos = int(tipologia[1:]) if tipologia else None

    # A freguesia não vem num campo próprio — quando existe, é a última parte
    # do título (ex. "Apartamento T2 na Rua X, Alvito, Alcântara"). Fica como
    # candidata; a validação central (build_dashboard_data.extract_freguesia)
    # descarta o que não parecer um topónimo.
    freguesia = None
    if titulo and "," in titulo:
        freguesia = titulo.rsplit(",", 1)[-1].strip() or None

    agencia_tag = article.select_one(".hightop-agent-name") or article.select_one("picture.logo-branding a[title]")
    agencia = None
    if agencia_tag:
        agencia = agencia_tag.get_text(strip=True) or agencia_tag.get("title")

    num_fotos = None
    counter = article.select_one(".item-multimedia-pictures__counter")
    if counter:
        spans = counter.find_all("span")
        if spans:
            digitos = re.sub(r"[^\d]", "", spans[-1].get_text(strip=True))
            num_fotos = int(digitos) if digitos else None

    desc_tag = article.select_one(".item-description p")
    descricao = desc_tag.get_text(" ", strip=True) if desc_tag else None

    return {
        "id": id_,
        "pagina": pagina,
        "titulo": titulo,
        "link": link,
        "tipologia": tipologia,
        "quartos": quartos,
        "tamanho": tamanho,
        "preco": preco,
        "preco_por_metro": round(preco / tamanho, 2) if preco and tamanho else None,
        "preco_antigo": preco_antigo,
        "desconto_pct": desconto_pct,
        "freguesia": freguesia,
        "concelho": concelho_nome,
        "distrito": "Lisboa",
        "agencia": agencia,
        "num_fotos": num_fotos,
        "descricao": descricao,
        "usufruto": tem_usufruto(f"{titulo or ''} {descricao or ''}"),
        "data_extracao": date.today().strftime("%Y%m%d"),
        "origem": "Idealista",
    }


def _url_pagina(path, page):
    base = BASE_URL.format(path=path)
    if page == 1:
        return base
    # Sem ".htm": o formato antigo "pagina-N.htm" devolve uma página vazia,
    # o formato dos links reais da paginação é "pagina-N".
    return f"{base}pagina-{page}"


class BloqueadoError(Exception):
    """O DataDome bloqueou a sessão (página sem anúncios com desafio presente)."""


def _fetch_page(driver, path, concelho_nome, page):
    """Devolve (items, tem_next) de uma página de resultados.

    Lança BloqueadoError se a página vier sem anúncios mas com o desafio do
    DataDome presente — melhor parar e reportar do que gravar vazios.
    """
    driver.get(_url_pagina(path, page))
    time.sleep(random.uniform(*REQUEST_DELAY_RANGE))

    soup = BeautifulSoup(driver.page_source, "html.parser")
    # "geo-reach-card" são sugestões de "nova construção a X km da tua zona"
    # que o Idealista injeta nos resultados — pertencem a concelhos vizinhos,
    # não à pesquisa atual, e não têm sequer link/preço; excluem-se.
    articles = [a for a in soup.select("article.item")
                if "geo-reach-card" not in (a.get("class") or [])
                and a.select_one("a.item-link")]
    tem_next = soup.select_one("div.pagination li.next a") is not None

    if not articles and "geo.captcha-delivery.com" in driver.page_source:
        raise BloqueadoError(
            f"[Idealista] DataDome bloqueou a sessão na página {page} de {path}."
        )

    items = [_parse_listing(a, concelho_nome, pagina=page) for a in articles]
    return items, tem_next


def _extrai_segmento(driver, path, seg_dir, concelho_nome, barra, max_paginas):
    """Pagina uma pesquisa (um concelho ou uma freguesia) até ao fim, gravando
    cada página em seg_dir/pagina_N.json (retomável). Devolve os anúncios."""
    os.makedirs(seg_dir, exist_ok=True)
    listagem = []
    page = 1
    while page <= max_paginas:
        page_file = os.path.join(seg_dir, f"pagina_{page}.json")
        if os.path.exists(page_file):
            with open(page_file, encoding="utf-8") as f:
                cached = json.load(f)
            listagem.extend(cached["items"])
            tem_next = cached["tem_next"]
            barra.set_postfix_str(f"pág {page} em cache")
        else:
            inicio = time.time()
            items, tem_next = _fetch_page(driver, path, concelho_nome, page)
            with open(page_file, "w", encoding="utf-8") as f:
                json.dump({"page": page, "tem_next": tem_next, "items": items}, f, ensure_ascii=False, indent=2)
            listagem.extend(items)
            barra.set_postfix_str(f"{len(items)} imóveis, {time.time()-inicio:.1f}s")
        barra.update(1)
        if not tem_next:
            break
        page += 1
    return listagem


def fetch_all_listings(concelho="lisboa", output_dir="idealista/json", driver=None,
                       max_paginas=400):
    """Extrai todos os apartamentos à venda de um concelho, página a página.

    Lisboa é dividida por freguesia (ver _segmentos / LISBOA_FREGUESIAS) para
    contornar o limite de paginação do Idealista; os restantes concelhos são
    uma pesquisa só. Cada página é gravada em disco à medida que avança
    (retomável); a lista final é deduplicada por id (anúncios que apareçam em
    mais do que uma freguesia contam uma vez).

    Aceita um driver já criado (para partilhar a mesma sessão de browser entre
    concelhos — ver scripts/run_idealista.py); se None, cria e fecha um próprio.
    """
    concelho_dir = os.path.join(output_dir, concelho)
    os.makedirs(concelho_dir, exist_ok=True)
    concelho_nome = _nome_concelho(concelho)
    segmentos = _segmentos(concelho)

    driver_proprio = driver is None
    if driver_proprio:
        driver = new_driver()

    all_listings = []
    barra = tqdm(desc=f"Idealista [{concelho}]", unit="página")
    try:
        for seg_id, path in segmentos:
            # Concelho de segmento único -> cache direto na pasta do concelho
            # (retrocompatível); Lisboa -> uma subpasta por freguesia.
            seg_dir = concelho_dir if len(segmentos) == 1 else os.path.join(concelho_dir, seg_id)
            if len(segmentos) > 1:
                barra.set_description(f"Idealista [{concelho}/{seg_id}]")
            all_listings.extend(_extrai_segmento(driver, path, seg_dir, concelho_nome, barra, max_paginas))
    finally:
        barra.close()
        if driver_proprio:
            driver.quit()

    # Dedup por id — o mesmo anúncio pode surgir em duas freguesias limítrofes.
    vistos, unicos = set(), []
    for it in all_listings:
        if it["id"] not in vistos:
            vistos.add(it["id"])
            unicos.append(it)

    consolidated_file = os.path.join(output_dir, f"{concelho}_{date.today().strftime('%Y%m%d')}.json")
    with open(consolidated_file, "w", encoding="utf-8") as f:
        json.dump(unicos, f, ensure_ascii=False, indent=2)
    logging.info(
        f"[Idealista] Total: {len(unicos)} imóveis únicos "
        f"({len(all_listings)-len(unicos)} duplicados removidos) consolidados em {consolidated_file}"
    )

    return unicos
