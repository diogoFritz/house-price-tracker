import requests
from bs4 import BeautifulSoup
import json
from models.Apartamento import Apartamento   # importa a classe
import logging

# Configuração do logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

# Ignorar avisos de certificado SSL (não recomendado para produção)
import urllib3
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

def write_to_html(body, filename="pagina_sapo.html"):
    with open(filename, "w", encoding="utf-8") as f:
        f.write(body.prettify())
    logging.info(f"Página salva como {filename}")

def read_sapo_html(filename="pagina_sapo.html"):
    with open(filename, "r", encoding="utf-8") as f:
        content = f.read()

    soup = BeautifulSoup(content, "html.parser")
    properties = soup.find_all("div", class_="property-info-content")
    logging.info(f"Encontradas {len(properties)} propriedades na página.")
    propriedades_lista = []
    for idx, prop in enumerate(properties, start=1):
        a_tag = prop.find("a", class_="property-info")
        link = a_tag["href"] if a_tag else None
        titulo = a_tag["title"] if a_tag else None
        propriedades_lista.append({
            "id": idx,
            "titulo": titulo,
            "link": link
        })
    
    # Salvar em ficheiro JSON
    with open("propriedades.json", "w", encoding="utf-8") as json_file:
        json.dump(propriedades_lista, json_file, ensure_ascii=False, indent=2)
    logging.info("Propriedades salvas em propriedades.json")


read_sapo_html()


def scrape_sapo_page():
    # Salvar o HTML da página em um arquivo
    url = f"https://casa.sapo.pt/comprar-apartamentos/lisboa/"
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
        write_to_html(soup)


# scrape_sapo_page()