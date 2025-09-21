import requests
from bs4 import BeautifulSoup
import json
from models.Apartamento import Apartamento   # importa a classe
import logging
from datetime import date
import re
import math

# Configuração do logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

# Ignorar avisos de certificado SSL (não recomendado para produção)
import urllib3
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

def write_to_html(body, filename="pagina_sapo.html"):
    with open(filename, "w", encoding="utf-8") as f:
        f.write(body.prettify())
    logging.info(f"Página salva como {filename}")

def read_sapo_html(filename):
    # Leitura do ficheiro HTML
    concelho = filename.split('_')[1]
    logging.info(f"Leitura do ficheiro {filename}")
    with open(filename, "r", encoding="utf-8") as f:
        content = f.read()

    # Extração de campos
    soup = BeautifulSoup(content, "html.parser")
    properties = soup.find_all("div", class_="property-info-content")
    logging.info(f"Encontradas {len(properties)} resultados em {concelho}")
    propriedades_lista = []

    # Total de casas (opcional)
    titulo_pesquisa = soup.find("div", class_="list-title").find("h1")
    total_casas = None
    if titulo_pesquisa:
        texto = titulo_pesquisa.get_text(strip=True)
        match = re.search(r"(\d+)", texto)
        if match:
            total_casas = int(match.group(1))
    logging.info(f"Total de apartamentos à venda: {total_casas}")

    for idx, prop in enumerate(properties, start=1):
        # Link e título
        a_tag = prop.find("a", class_="property-info")
        link = a_tag["href"] if a_tag else None
        link =f'https://casa.sapo.pt/comprar-apartamentos/{concelho}' + link
        titulo = a_tag["title"] if a_tag else None

        # Preço
        preco_tag = prop.find("div", class_="property-price-value")
        preco = None
        if preco_tag:
            preco = re.sub(r"[^\d]", "", preco_tag.get_text(strip=True))
            preco = int(preco) if preco else None

        # Estado e Tamanho
        features_tag = prop.find("div", class_="property-features-text")
        estado, tamanho = None, None
        if features_tag:
            txt = features_tag.get_text(strip=True)
            partes = [p.strip() for p in txt.split("·")]
            if len(partes) >= 1:
                estado = partes[0]  # "Novo", "Usado"
            if len(partes) >= 2 and "m²" in partes[1]:
                tamanho = re.sub(r"[^\d]", "", partes[1])
                tamanho = int(tamanho) if tamanho else None

        # Tipologia (regex no título)
        tipologia = None
        if titulo:
            m = re.search(r"T\d+", titulo)
            tipologia = m.group(0) if m else None

        # Localização
        local_tag = prop.find("div", class_="property-location")
        localizacao = local_tag.get_text(strip=True) if local_tag else None

        # Data de publicação
        data_tag = prop.find("div", class_="property-date")
        data_pub = data_tag.get_text(strip=True) if data_tag else None

        # Preço por metro
        preco_por_metro = None
        if preco and tamanho:
            preco_por_metro = round(preco / tamanho, 2)

    # Preço
    preco, preco_antigo, desconto = None, None, None

    # Preço antigo (se existir)
    preco_old_tag = prop.find("div", class_="property-price-old")
    if preco_old_tag:
        preco_antigo = re.sub(r"[^\d]", "", preco_old_tag.get_text(strip=True))
        preco_antigo = int(preco_antigo) if preco_antigo else None

    # Preço atual
    preco_tag = prop.find("div", class_="property-price-value")
    if preco_tag:
        preco = re.sub(r"[^\d]", "", preco_tag.get_text(strip=True))
        preco = int(preco) if preco else None

    # Desconto (%)
    desconto_tag = prop.find("div", class_="property-price-discount")
    if desconto_tag:
        desconto = desconto_tag.get_text(strip=True)  # ex: "-9%"

    # ...
    propriedades_lista.append({
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
        "data_publicacao": data_pub
    })

    # Escrita para ficheiro JSON
    date_format = date.today().strftime("%Y_%m_%d")
    file_name = 'ficheiros/propriedades_sapo_' + date_format + '.json'
    logging.info(f"Salvando dados em {file_name}")
    with open(file_name, "w", encoding="utf-8") as json_file:
        json.dump(propriedades_lista, json_file, ensure_ascii=False, indent=2)
    logging.info(f"{file_name} salvas em ficheiros")
    

def scrape_sapo_page(concelho='lisboa'):
    # Salvar o HTML da página em um arquivo
    url = f"https://casa.sapo.pt/comprar-apartamentos/{concelho}/"
    headers = {"User-Agent": "Mozilla/5.0"}
    response = requests.get(url, headers=headers,verify=False)

    if response.status_code != 200:
        logging.error(f"Erro ao aceder à página {url}")
        return []
    else:
        logging.info(f"Acedeu com sucesso à página {url}")
        soup = BeautifulSoup(response.text, "html.parser")
        # Exemplo: buscar títulos e preços dos anúncios
        anuncios = soup.find_all("div", class_="list-content-properties") #list-content-properties
        # logging.info(soup.prettify())
        date_format = date.today().strftime("%Y_%m_%d")
        write_to_html(soup, filename=f"sapo_{concelho}_{date_format}.html")


def scrape_sapo_pages(concelho="amadora", max_pages=5):
    base_url = f"https://casa.sapo.pt/comprar-apartamentos/{concelho}/?pn={{}}"
    headers = {"User-Agent": "Mozilla/5.0"}
    todas_props = []

    for page in range(1, max_pages + 1):
        url = base_url.format(page)
        resp = requests.get(url, headers=headers, verify=False)

        if resp.status_code != 200:
            logging.error(f"Erro ao aceder à página {page} ({url})")
            break

        soup = BeautifulSoup(resp.text, "html.parser")
        properties = soup.find_all("div", class_="property-info-content")
        logging.info(f"Página {page}: {len(properties)} resultados detetados")

        if not properties:
            logging.info(f"Fim das páginas em {page}")
            break

        # Aqui reutilizas o teu parser de propriedades
        for idx, prop in enumerate(properties, start=1):
            dados = {"pagina": page, "id": idx}  # simplificado
            todas_props.append(dados)

    logging.info(f"Total global de propriedades extraídas: {len(todas_props)}")
    return todas_props


# run in terminal python scripts/scraping_sapo.py

# Extrair html de pagina sapo
# scrape_sapo_page(concelho='lisboa')
# scrape_sapo_page(concelho='amadora')
# scrape_sapo_page(concelho='loures')

# data = read_sapo_html('sapo_amadora_2025_09_21.html')
# print(data)

# scrape_sapo_pages(concelho="amadora", max_pages=5)

#write_to_json(data)

def get_sapo_results(concelho="amadora"):
    url = f"https://casa.sapo.pt/comprar-apartamentos/{concelho}/?pn=1"
    headers = {"User-Agent": "Mozilla/5.0"}
    resp = requests.get(url, headers=headers, verify=False)

    if resp.status_code != 200:
        logging.error(f"Erro ao aceder à página {url}")
        return None, None, None

    soup = BeautifulSoup(resp.text, "html.parser")

    # 1) Total de casas (do título)
    total_casas = None
    titulo_pesquisa = soup.find("div", class_="list-title")
    if titulo_pesquisa:
        texto = titulo_pesquisa.get_text(strip=True)
        match = re.search(r"(\d+)", texto)
        if match:
            total_casas = int(match.group(1))

    # 2) Nº resultados na primeira página
    properties = soup.find_all("div", class_="property-info-content")
    resultados_por_pagina = len(properties)

    # 3) Total de páginas
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

    return total_paginas, total_casas, resultados_por_pagina

# pages = get_sapo_results(concelho="amadora")
data = scrape_sapo_pages(concelho="amadora", max_pages=5)
print(data)
# write_to_html(data)
