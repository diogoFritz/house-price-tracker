"""Scraper para o Century21.

Tal como o Remax, os resultados vêm de uma API JSON interna
(GET /api/properties) sem qualquer proteção anti-bot — basta replicar o
pedido que o próprio site faz.
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

API_URL = "https://www.century21.pt/api/properties"

HEADERS = {
    "Accept": "application/json",
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
}

# Código de distrito+concelho do Century21 (padrão INE) por concelho — vão-se
# acrescentando à medida que se descobrem (ex.: a partir do URL de pesquisa
# desse concelho no site, tal como se fez para "lisboa").
ADDRESS_IDS = {
    "lisboa": "1106",
    "amadora": "1115",
    "loures": "1107",
    "odivelas": "1116",
    "oeiras": "1110",
    "sintra": "1111",
}

PAGE_SIZE = 100

AUTOCOMPLETE_URL = "https://www.century21.pt/api/autocomplete"

# Todos os concelhos suportados pertencem ao distrito de Lisboa.
DISTRITO = "Lisboa"


def _freguesia_lookup(concelho):
    """Devolve {codigo_ad_3_id: nome_freguesia} para um concelho, via a mesma API
    de autocompletar do campo de pesquisa do site. O nome vem como "Freguesia,
    Concelho" — guarda-se só a parte da freguesia."""
    params = {"searchrefs": "", "q": concelho, "limit": 50}
    r = requests.get(AUTOCOMPLETE_URL, headers=HEADERS, params=params, timeout=15, verify=False)
    r.raise_for_status()
    return {
        item["code"]: item["name"].split(",")[0].strip()
        for item in r.json()
        if item.get("level") == 3
    }


def _parse_listing(item, freguesias, concelho_nome, pagina=None):
    tamanho = item.get("useful_area") or item.get("gross_area")
    preco = item.get("price")
    quartos = item.get("number_of_rooms")

    return {
        "id": item.get("id"),
        "pagina": pagina,
        "titulo": (item.get("title") or {}).get("pt"),
        "link": item.get("link"),
        "tipologia": f"T{quartos}" if isinstance(quartos, int) else None,
        "quartos": quartos,
        "tamanho": tamanho,
        "preco": preco,
        "preco_por_metro": round(preco / tamanho, 2) if preco and tamanho else None,
        "morada": item.get("address"),
        "freguesia": freguesias.get(item.get("ad_3_id")),
        "concelho": concelho_nome,
        "distrito": DISTRITO,
        "agencia": (item.get("agency") or {}).get("name"),
        "agente": (item.get("agent") or {}).get("name"),
        "data_publicacao": item.get("entered_market"),
        "data_extracao": date.today().strftime("%Y%m%d"),
        "origem": "Century21",
    }


def _fetch_page_data(concelho, page, freguesias, page_size=PAGE_SIZE):
    """Devolve (items_parsed, total) de uma página de resultados, ou (None, None) se falhar."""
    address_id = ADDRESS_IDS.get(concelho)
    if address_id is None:
        raise ValueError(
            f"Address ID desconhecido para concelho={concelho!r}. "
            f"Concelhos suportados: {sorted(ADDRESS_IDS)}."
        )

    params = {
        "addresses": address_id,
        "address_names": concelho.capitalize(),
        "ad_type": "sell",
        "asset_type": "apartment",
        "limit": page_size,
        "page": page,
    }
    r = requests.get(API_URL, headers=HEADERS, params=params, timeout=30, verify=False)
    if r.status_code != 200:
        logging.error(f"[Century21] Erro {r.status_code} na página {page} (concelho={concelho})")
        return None, None

    data = r.json()
    items = [
        _parse_listing(item, freguesias, concelho.capitalize(), pagina=page)
        for item in data.get("data", [])
    ]
    return items, data.get("total")


def fetch_all_listings(concelho="lisboa", output_dir="century21/json"):
    """Extrai todos os imóveis de um concelho, percorrendo todas as páginas da API.

    Grava o resultado de cada página em disco (output_dir/<concelho>/pagina_N.json)
    à medida que avança — retomável: se a extração for interrompida, corre outra
    vez com os mesmos argumentos para retomar a partir da última página em falta.
    """
    concelho_dir = os.path.join(output_dir, concelho)
    os.makedirs(concelho_dir, exist_ok=True)

    freguesias = _freguesia_lookup(concelho)
    logging.info(f"[Century21] {len(freguesias)} freguesias encontradas para {concelho}.")

    all_listings = []
    total = None
    page = 1
    barra = tqdm(desc=f"Century21 [{concelho}]", unit="página")

    while True:
        if total is not None and (page - 1) * PAGE_SIZE >= total:
            break

        page_file = os.path.join(concelho_dir, f"pagina_{page}.json")

        if os.path.exists(page_file):
            with open(page_file, encoding="utf-8") as f:
                cached = json.load(f)
            all_listings.extend(cached["items"])
            total = cached["total"]
            if barra.total is None:
                barra.total = -(-total // PAGE_SIZE)
                barra.refresh()
            barra.set_postfix_str(f"página {page} em cache")
            barra.update(1)
            page += 1
            continue

        inicio = time.time()
        items, total_encontrado = _fetch_page_data(concelho, page, freguesias)
        duracao = time.time() - inicio
        if items is None:
            tqdm.write(
                f"[Century21] Falha na página {page} ({duracao:.1f}s). Progresso gravado até à página {page - 1}. "
                "Chama fetch_all_listings outra vez mais tarde para retomar a partir daqui."
            )
            break
        if not items:
            break

        total = total_encontrado
        if barra.total is None:
            barra.total = -(-total // PAGE_SIZE)
            barra.refresh()
        with open(page_file, "w", encoding="utf-8") as f:
            json.dump({"page": page, "total": total, "items": items}, f, ensure_ascii=False, indent=2)

        all_listings.extend(items)
        barra.set_postfix_str(f"{len(items)} imóveis, {duracao:.1f}s")
        barra.update(1)
        page += 1

    barra.close()

    consolidated_file = os.path.join(output_dir, f"{concelho}_{date.today().strftime('%Y%m%d')}.json")
    with open(consolidated_file, "w", encoding="utf-8") as f:
        json.dump(all_listings, f, ensure_ascii=False, indent=2)
    logging.info(f"[Century21] Total: {len(all_listings)} imóveis consolidados em {consolidated_file}")

    return all_listings
