"""Scraper para o ERA.

Tal como o Remax/Century21, os resultados vêm de uma API JSON interna
(POST /API/ServicesModule/Property/Search), mas esta exige um token
anti-forgery (__RequestVerificationToken) e um TabId — ambos extraídos da
página HTML inicial e reutilizados (via sessão/cookies) nos pedidos seguintes.
"""
import json
import logging
import os
import re
import time
from datetime import date

import requests
from tqdm import tqdm

import urllib3

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

SEARCH_URL = "https://www.era.pt/API/ServicesModule/Property/Search"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "pt-PT,pt;q=0.9",
}

MODULE_ID = "410"

# Código distrito-concelho (padrão INE) por concelho — vão-se acrescentando à
# medida que se descobrem (a partir do URL de pesquisa desse concelho no site).
LOCATION_IDS = {
    "lisboa": "11-06",
    "amadora": "11-15",
    "loures": "11-07",
    "odivelas": "11-16",
    "oeiras": "11-10",
    "sintra": "11-11",
    "alenquer": "11-01",
    "arruda-dos-vinhos": "11-02",
    "azambuja": "11-03",
    "cadaval": "11-04",
    "cascais": "11-05",
    "lourinha": "11-08",
    "mafra": "11-09",
    "sobral-de-monte-agraco": "11-12",
    "torres-vedras": "11-13",
    "vila-franca-de-xira": "11-14",
}

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


def _pagina_url(concelho):
    return f"https://www.era.pt/imoveis/comprar/apartamentos/lisboa/{concelho}"


def _nova_sessao(concelho):
    """Cria uma sessão com o token anti-forgery e o TabId extraídos da página
    inicial — necessários em todos os pedidos seguintes à API de pesquisa."""
    session = requests.Session()
    r = session.get(_pagina_url(concelho), headers=HEADERS, timeout=30, verify=False)
    r.raise_for_status()

    m_token = re.search(r'__RequestVerificationToken" type="hidden" value="([^"]+)"', r.text)
    m_tab = re.search(r"sf_tabId.:.(\d+).", r.text)
    if not m_token or not m_tab:
        raise RuntimeError(f"[ERA] Não encontrei o token anti-forgery ou o TabId na página de {concelho}.")

    session.headers.update({
        **HEADERS,
        "Content-Type": "application/json",
        "Accept": "*/*",
        "ModuleId": MODULE_ID,
        "TabId": m_tab.group(1),
        "RequestVerificationToken": m_token.group(1),
        "X-Requested-With": "XMLHttpRequest",
        "Referer": "https://www.era.pt/comprar/apartamentos/lisboa",
    })
    return session


def _parse_preco(sell_price):
    if not sell_price or not sell_price.get("Value"):
        return None
    digitos = re.sub(r"[^\d]", "", sell_price["Value"])
    return int(digitos) if digitos else None


def _parse_coord(valor):
    try:
        return float(str(valor).replace(",", "."))
    except (TypeError, ValueError):
        return None


def _parse_area(valor):
    """Área vem por vezes como número, por vezes como string (ou string vazia)."""
    try:
        area = float(str(valor).replace(",", "."))
        return area if area > 0 else None
    except (TypeError, ValueError):
        return None


def _parse_listing(item, concelho_nome, pagina=None):
    tamanho = _parse_area(item.get("ListingArea")) or _parse_area(item.get("NetArea"))
    preco = _parse_preco(item.get("SellPrice"))
    # "Rooms" vem como string (ex. "2"), não int.
    quartos_txt = item.get("Rooms")
    quartos = int(quartos_txt) if isinstance(quartos_txt, str) and quartos_txt.isdigit() else None

    # "Localization" vem como "Freguesia, Distrito" (não concelho) — o concelho
    # já é conhecido (passado como argumento), por isso só se extrai a freguesia.
    localizacao = item.get("Localization") or ""
    freguesia = localizacao.split(",")[0].strip() if "," in localizacao else None

    return {
        "id": item.get("Id"),
        "pagina": pagina,
        "titulo": item.get("Title"),
        "link": item.get("DetailUrl"),
        "tipologia": f"T{quartos}" if quartos is not None else None,
        "quartos": quartos,
        "tamanho": tamanho,
        "preco": preco,
        "preco_por_metro": round(preco / tamanho, 2) if preco and tamanho else None,
        "piso": item.get("Floor"),
        "freguesia": freguesia,
        "concelho": concelho_nome,
        "distrito": "Lisboa",
        "agencia": (item.get("RealEstate") or {}).get("Name"),
        "lat": _parse_coord(item.get("Lat")),
        "lng": _parse_coord(item.get("Lng")),
        "data_extracao": date.today().strftime("%Y%m%d"),
        "origem": "ERA",
    }


def _fetch_page_data(session, concelho, page):
    """Devolve (items_parsed, total_pages) de uma página de resultados, ou (None, None) se falhar."""
    location_id = LOCATION_IDS.get(concelho)
    if location_id is None:
        raise ValueError(
            f"Location ID desconhecido para concelho={concelho!r}. "
            f"Concelhos suportados: {sorted(LOCATION_IDS)}."
        )

    payload = {
        "page": page,
        "propertiesTypeId": [1],  # 1 = Apartamento
        "locationId": [location_id],
        "onlyDevelopments": False,
        "order": 3,
        "isResidential": True,
        "nonResidential": False,
    }
    r = session.post(SEARCH_URL, json=payload, timeout=30, verify=False)
    if r.status_code != 200:
        logging.error(f"[ERA] Erro {r.status_code} na página {page} (concelho={concelho})")
        return None, None

    data = r.json()
    items = [
        _parse_listing(item, _nome_concelho(concelho), pagina=page)
        for item in data.get("PropertyList", [])
    ]
    return items, data.get("TotalPages")


def fetch_all_listings(concelho="lisboa", output_dir="era/json"):
    """Extrai todos os imóveis de um concelho, percorrendo todas as páginas da API.

    Grava o resultado de cada página em disco (output_dir/<concelho>/pagina_N.json)
    à medida que avança — retomável: se a extração for interrompida, corre outra
    vez com os mesmos argumentos para retomar a partir da última página em falta.
    """
    concelho_dir = os.path.join(output_dir, concelho)
    os.makedirs(concelho_dir, exist_ok=True)

    session = _nova_sessao(concelho)

    all_listings = []
    total_pages = None
    page = 1
    barra = tqdm(desc=f"ERA [{concelho}]", unit="página")

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
        items, total_pages_encontradas = _fetch_page_data(session, concelho, page)
        duracao = time.time() - inicio
        if items is None:
            tqdm.write(
                f"[ERA] Falha na página {page} ({duracao:.1f}s). Progresso gravado até à página {page - 1}. "
                "Chama fetch_all_listings outra vez mais tarde para retomar a partir daqui."
            )
            break

        total_pages = total_pages_encontradas
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
    logging.info(f"[ERA] Total: {len(all_listings)} imóveis consolidados em {consolidated_file}")

    return all_listings
