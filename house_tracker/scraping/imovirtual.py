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

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

# Intervalo entre pedidos sucessivos ao percorrer várias páginas, para não
# parecer tráfego automatizado e reduzir o risco de bloqueio/captcha (DataDome).
REQUEST_DELAY_RANGE = (2.0, 4.0)

_ROOMS_MAP = {
    "ONE": 1, "TWO": 2, "THREE": 3, "FOUR": 4, "FIVE": 5,
    "SIX": 6, "SEVEN": 7, "EIGHT": 8, "NINE": 9, "TEN": 10,
}

# Rés-do-chão = 0, cave = -1. ABOVE_TENTH não tem número exato no site — aproximado a 11.
_PISO_MAP = {
    "CELLAR": -1, "GROUND": 0, "FIRST": 1, "SECOND": 2, "THIRD": 3, "FOURTH": 4,
    "FIFTH": 5, "SIXTH": 6, "SEVENTH": 7, "EIGHTH": 8, "NINTH": 9, "TENTH": 10,
    "ABOVE_TENTH": 11,
}

DOWNLOAD_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:109.0) Gecko/20100101 Firefox/117.0",
    "Accept-Language": "pt-PT,pt;q=0.9,en-US;q=0.8,en;q=0.7",
    "Accept-Encoding": "gzip, deflate, br",
    "Connection": "keep-alive",
    "Upgrade-Insecure-Requests": "1",
}

JSONLD_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "pt-PT,pt;q=0.9,en;q=0.8",
}


def download_imovirtual(url, filename="imovirtual.html"):
    """Descarrega uma página de resultados do Imovirtual e guarda-a em HTML local."""
    r = requests.get(url, headers=DOWNLOAD_HEADERS, timeout=30, verify=False)
    r.raise_for_status()

    with open(filename, "w", encoding="utf-8") as f:
        f.write(r.text)

    return filename


def fetch_json_ld(url, timeout=30):
    """Faz GET ao URL, não guarda o HTML, e devolve todos os blocos application/ld+json como dicts."""
    r = requests.get(url, headers=JSONLD_HEADERS, timeout=timeout, verify=False)
    r.raise_for_status()
    soup = BeautifulSoup(r.text, "html.parser")

    blocks = []
    for script in soup.find_all("script", {"type": "application/ld+json"}):
        if not script.string:
            continue
        text = script.string.strip()
        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            try:
                cleaned = re.sub(r'(\w+):', r'"\1":', text)
                data = json.loads(cleaned)
            except json.JSONDecodeError:
                continue

        if isinstance(data, list):
            blocks.extend(data)
        else:
            blocks.append(data)

    return blocks


def _location_field(item, level):
    locations = item.get("location", {}).get("reverseGeocoding", {}).get("locations", [])
    for loc in locations:
        if loc.get("locationLevel") == level:
            return loc.get("name")
    return None


def _parse_listing(item, pagina=None):
    total_price = item.get("totalPrice") or {}
    price_m2 = item.get("pricePerSquareMeter") or {}
    slug = item.get("slug") or ""
    link = f"https://www.imovirtual.com/pt/anuncio/{slug}" if slug else None

    quartos = _ROOMS_MAP.get(item.get("roomsNumber"), item.get("roomsNumber"))
    tipologia = f"T{quartos - 1}" if isinstance(quartos, int) else None

    street = item.get("location", {}).get("address", {}).get("street", {}) or {}
    morada = " ".join(part for part in [street.get("name"), street.get("number")] if part).strip() or None

    return {
        "id": item.get("id"),
        "pagina": pagina,
        "titulo": item.get("title"),
        "link": link,
        "tipologia": tipologia,
        "quartos": quartos,
        "tamanho": item.get("areaInSquareMeters"),
        "preco": total_price.get("value"),
        "preco_por_metro": price_m2.get("value"),
        "piso": _PISO_MAP.get(item.get("floorNumber"), item.get("floorNumber")),
        "morada": morada,
        "bairro": _location_field(item, "neighborhood"),
        "freguesia": _location_field(item, "parish"),
        "concelho": _location_field(item, "council"),
        "distrito": _location_field(item, "district"),
        "agencia": (item.get("agency") or {}).get("name"),
        # Quando não há agência formal (privado ou promotor de um empreendimento
        # novo), o nome do anunciante vem em "advertOwner", não em "agency".
        "anunciante": (item.get("advertOwner") or {}).get("name"),
        "destacado": item.get("isPromoted"),
        "oferta_privada": item.get("isPrivateOwner"),
        "num_fotos": item.get("totalPossibleImages"),
        "data_publicacao": item.get("dateCreated"),
        "data_extracao": date.today().strftime("%Y%m%d"),
        "origem": "Imovirtual",
    }


def _fetch_page_data(concelho, distrito, page):
    """Devolve (items_parsed, pagination) de uma página de resultados, ou (None, None) se bloqueado."""
    url = f"https://www.imovirtual.com/pt/resultados/comprar/apartamento/{distrito}/{concelho}/?page={page}"

    r = requests.get(url, headers=DOWNLOAD_HEADERS, timeout=30, verify=False)
    r.raise_for_status()

    soup = BeautifulSoup(r.text, "html.parser")
    script = soup.find("script", {"id": "__NEXT_DATA__"})
    if script is None or not script.string:
        logging.error(
            f"[Imovirtual] __NEXT_DATA__ não encontrado em {url} — "
            "possível bloqueio/captcha (o site usa DataDome)."
        )
        return None, None

    data = json.loads(script.string)
    search_ads = (
        data.get("props", {}).get("pageProps", {}).get("data", {}).get("searchAds", {})
    )
    items = search_ads.get("items", [])
    pagination = search_ads.get("pagination", {})
    return [_parse_listing(item, pagina=page) for item in items], pagination


def fetch_listings(concelho="lisboa", distrito=None, page=1):
    """Obtém os imóveis (dados estruturados) de uma página de resultados do Imovirtual.

    Os dados reais dos anúncios vêm do JSON embutido em <script id="__NEXT_DATA__">
    (app Next.js) — o bloco application/ld+json só tem metadados da página, não
    os imóveis individuais.
    """
    distrito = distrito or concelho
    items, _ = _fetch_page_data(concelho, distrito, page)
    if items is None:
        return []

    if not items:
        logging.warning(f"[Imovirtual] 0 imóveis extraídos (concelho={concelho}, página={page}) "
                         "— verificar se houve bloqueio/captcha.")
    else:
        logging.info(f"[Imovirtual] {len(items)} imóveis encontrados (concelho={concelho}, página={page})")

    return items


def fetch_all_listings(concelho="lisboa", distrito=None, output_dir="imovirtual/json"):
    """Extrai todos os imóveis de um concelho, percorrendo todas as páginas.

    Grava o resultado de cada página em disco (output_dir/<concelho>/pagina_N.json)
    à medida que avança. Se a extração for interrompida (erro, bloqueio/captcha),
    basta chamar a função outra vez com os mesmos argumentos: as páginas já gravadas
    são reaproveitadas em vez de repedidas, retomando a partir da última página em falta.
    """
    distrito = distrito or concelho
    concelho_dir = os.path.join(output_dir, concelho)
    os.makedirs(concelho_dir, exist_ok=True)

    all_listings = []
    total_pages = None
    page = 1
    barra = tqdm(desc=f"Imovirtual [{concelho}]", unit="página")

    while total_pages is None or page <= total_pages:
        page_file = os.path.join(concelho_dir, f"pagina_{page}.json")

        if os.path.exists(page_file):
            with open(page_file, encoding="utf-8") as f:
                cached = json.load(f)
            all_listings.extend(cached["items"])
            total_pages = cached["total_pages"]
            if barra.total is None:
                barra.total = total_pages
                barra.refresh()
            barra.set_postfix_str(f"página {page} em cache")
            barra.update(1)
            page += 1
            continue

        if page > 1:
            time.sleep(random.uniform(*REQUEST_DELAY_RANGE))

        inicio = time.time()
        items, pagination = _fetch_page_data(concelho, distrito, page)
        duracao = time.time() - inicio
        if items is None:
            tqdm.write(
                f"[Imovirtual] Falha/bloqueio na página {page} ({duracao:.1f}s). Progresso gravado até à página {page - 1}. "
                "Chama fetch_all_listings outra vez mais tarde para retomar a partir daqui."
            )
            break

        total_pages = pagination.get("totalPages", total_pages)
        if barra.total is None:
            barra.total = total_pages
            barra.refresh()
        with open(page_file, "w", encoding="utf-8") as f:
            json.dump({"page": page, "total_pages": total_pages, "items": items}, f, ensure_ascii=False, indent=2)

        all_listings.extend(items)
        barra.set_postfix_str(f"{len(items)} imóveis, {duracao:.1f}s")
        barra.update(1)
        page += 1

    barra.close()

    consolidated_file = os.path.join(output_dir, f"{concelho}_{date.today().strftime('%Y%m%d')}.json")
    with open(consolidated_file, "w", encoding="utf-8") as f:
        json.dump(all_listings, f, ensure_ascii=False, indent=2)
    logging.info(f"[Imovirtual] Total: {len(all_listings)} imóveis consolidados em {consolidated_file}")

    return all_listings
