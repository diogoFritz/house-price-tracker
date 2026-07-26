"""Scraper para o Remax.

Ao contrário do Sapo/Imovirtual (HTML) e do Idealista (protegido por DataDome),
o Remax carrega os resultados via uma API JSON interna
(POST /api/Listing/PaginatedMultiMatchSearch) sem qualquer proteção anti-bot —
basta replicar o pedido que o próprio site faz.
"""
import json
import logging
import os
import time
from datetime import date

import requests
from tqdm import tqdm

import urllib3

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

API_URL = "https://www.remax.pt/api/Listing/PaginatedMultiMatchSearch"

HEADERS = {
    "Content-Type": "application/json",
    "Accept": "application/json, text/plain, */*",
    "Languageid": "9",
    "TenantId": "7",
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
}

# Region2ID do Remax por concelho — não há um endpoint óbvio de lookup, por isso
# vão-se acrescentando à medida que se descobrem (ex.: a partir do URL de pesquisa
# desse concelho no site, tal como se fez para "lisboa").
REGION_IDS = {
    "lisboa": "537",
    "amadora": "532",
    "loures": "538",
    "odivelas": "965",
    "oeiras": "541",
    "sintra": "542",
    "alenquer": "531",
    "arruda-dos-vinhos": "533",
    "azambuja": "534",
    "cadaval": "535",
    "cascais": "536",
    "lourinha": "539",
    "mafra": "540",
    "sobral-de-monte-agraco": "543",
    "torres-vedras": "544",
    "vila-franca-de-xira": "545",
}

PAGE_SIZE = 100


def _filters(concelho):
    region_id = REGION_IDS.get(concelho)
    if region_id is None:
        raise ValueError(
            f"Region2ID desconhecido para concelho={concelho!r}. "
            f"Concelhos suportados: {sorted(REGION_IDS)}."
        )
    return [
        {"field": "businessTypeID", "operationType": "int", "operator": "=", "value": "1", "label": "buy"},
        {"field": "Region2ID", "operationType": "string", "operator": "=", "value": region_id},
        {"field": "listingTypeID", "operationType": "multiple", "operator": "=", "value": "1"},
        {"field": "listingClassID", "operationType": "int", "operator": "=", "value": "1"},
        {"field": "isSpecialExclusive", "operator": "=", "operationType": "string", "value": "false"},
    ]


def _parse_listing(item, pagina=None):
    tamanho = item.get("totalArea") or item.get("livingArea")
    preco = item.get("listingPrice")
    quartos = item.get("numberOfBedrooms")

    slug = item.get("descriptionTags")
    listing_title = item.get("listingTitle")
    link = f"https://www.remax.pt/pt/imoveis/{slug}/{listing_title}" if slug and listing_title else None

    return {
        "id": item.get("id"),
        "pagina": pagina,
        "titulo": slug,
        "link": link,
        "tipologia": f"T{quartos}" if isinstance(quartos, int) else None,
        "quartos": quartos,
        "tamanho": tamanho,
        "preco": preco,
        "preco_por_metro": round(preco / tamanho, 2) if preco and tamanho else None,
        "piso": item.get("floorAsNumber"),
        "morada": item.get("address"),
        "freguesia": item.get("regionName3"),
        "concelho": item.get("regionName2"),
        "distrito": item.get("regionName1"),
        "agencia": item.get("officeName"),
        "agente": item.get("userName"),
        "num_fotos": len(item.get("listingPictures") or []),
        "data_publicacao": item.get("publishDate"),
        "data_extracao": date.today().strftime("%Y%m%d"),
        "origem": "Remax",
    }


def _fetch_page_data(concelho, page, page_size=PAGE_SIZE):
    """Devolve (items_parsed, total_pages) de uma página de resultados, ou (None, None) se falhar."""
    payload = {
        "filters": _filters(concelho),
        "pageNumber": page,
        "pageSize": page_size,
        "sort": ["-PublishDate"],
        "searchValue": concelho.capitalize(),
    }
    r = requests.post(API_URL, headers=HEADERS, json=payload, timeout=30, verify=False)
    if r.status_code != 200:
        logging.error(f"[Remax] Erro {r.status_code} na página {page} (concelho={concelho})")
        return None, None

    data = r.json()
    if not data.get("isValid", True):
        logging.error(f"[Remax] Resposta inválida na página {page}: {data.get('errorMessage')}")
        return None, None

    items = [_parse_listing(item, pagina=page) for item in data.get("results", [])]
    return items, data.get("totalPages")


def fetch_all_listings(concelho="lisboa", output_dir="remax/json"):
    """Extrai todos os imóveis de um concelho, percorrendo todas as páginas da API.

    Grava o resultado de cada página em disco (output_dir/<concelho>/pagina_N.json)
    à medida que avança — retomável: se a extração for interrompida, corre outra
    vez com os mesmos argumentos para retomar a partir da última página em falta.
    """
    concelho_dir = os.path.join(output_dir, concelho)
    os.makedirs(concelho_dir, exist_ok=True)

    all_listings = []
    total_pages = None
    page = 1
    barra = tqdm(desc=f"Remax [{concelho}]", unit="página")

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

        inicio = time.time()
        items, pages_encontradas = _fetch_page_data(concelho, page)
        duracao = time.time() - inicio
        if items is None:
            tqdm.write(
                f"[Remax] Falha na página {page} ({duracao:.1f}s). Progresso gravado até à página {page - 1}. "
                "Chama fetch_all_listings outra vez mais tarde para retomar a partir daqui."
            )
            break

        total_pages = pages_encontradas
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
    logging.info(f"[Remax] Total: {len(all_listings)} imóveis consolidados em {consolidated_file}")

    return all_listings
