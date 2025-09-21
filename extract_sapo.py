from  utils import logging, date, json, re, BeautifulSoup, requests,math,pd
from pathlib import Path
import sqlite3
# Configuração do logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

# Ignorar avisos de certificado SSL (não recomendado para produção)
import urllib3
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)


def json_to_table(json_file, output_dir="ficheiros"):
    """
    Lê um ficheiro JSON de propriedades e converte em formato tabular.
    Exporta também para CSV e Excel.
    """
    logging.info(f"Lendo ficheiro {json_file}")
    with open(json_file, "r", encoding="utf-8") as f:
        data = json.load(f)

    # Converter lista de dicionários em DataFrame
    df = pd.DataFrame(data)

    logging.info(f"DataFrame criado com {len(df)} registos e {len(df.columns)} colunas")

    # Criar pasta de saída (se não existir)
    Path(output_dir).mkdir(parents=True, exist_ok=True)

    # Nomes dos ficheiros de saída
    base_name = Path(json_file).stem
    csv_file = Path(output_dir) / f"{base_name}.csv"

    # Exportar
    df.to_csv(csv_file, index=False, encoding="utf-8-sig")

    logging.info(f"Tabela salva em {csv_file}")

    return df

# def json_to_table(json_file):
#     """
#     Lê um ficheiro JSON de propriedades e devolve um DataFrame tabular.
#     """
#     logging.info(f"Lendo ficheiro {json_file}")
#     with open(json_file, "r", encoding="utf-8") as f:
#         data = json.load(f)

#     # Converter lista de dicionários em DataFrame
#     df = pd.DataFrame(data)

#     logging.info(f"DataFrame criado com {len(df)} registos e {len(df.columns)} colunas")
#     return df

def scrape_sapo_site(concelho="amadora"):
    base_url = f"https://casa.sapo.pt/comprar-apartamentos/{concelho}/?pn={{}}"
    headers = {"User-Agent": "Mozilla/5.0"}

    # 1) Obter a primeira página para calcular total de páginas
    resp = requests.get(base_url.format(1), headers=headers, verify=False)
    if resp.status_code != 200:
        logging.error(f"Erro ao aceder à página 1 ({base_url.format(1)})")
        return

    soup = BeautifulSoup(resp.text, "html.parser")

    # Total de casas
    total_casas = None
    titulo_pesquisa = soup.find("div", class_="list-title")
    if titulo_pesquisa:
        texto = titulo_pesquisa.get_text(strip=True)
        match = re.search(r"(\d+)", texto)
        if match:
            total_casas = int(match.group(1))

    # Nº resultados por página
    properties = soup.find_all("div", class_="property-info-content")
    resultados_por_pagina = len(properties)

    # Total de páginas
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

    propriedades_lista = []

    # 2) Loop por todas as páginas
    for page in range(1, total_paginas + 1):
        url = base_url.format(page)
        resp = requests.get(url, headers=headers, verify=False)
        if resp.status_code != 200:
            logging.error(f"Erro ao aceder à página {page} ({url})")
            continue

        soup = BeautifulSoup(resp.text, "html.parser")
        properties = soup.find_all("div", class_="property-info-content")
        logging.info(f"Página {page}: {len(properties)} resultados detetados")

        for idx, prop in enumerate(properties, start=1):
            # Link e título
            a_tag = prop.find("a", class_="property-info")
            link = a_tag["href"] if a_tag else None
            if link and link.startswith("/"):
                link = f"https://casa.sapo.pt{link}"
            titulo = a_tag["title"] if a_tag else None

            # Estado e Tamanho
            features_tag = prop.find("div", class_="property-features-text")
            estado, tamanho = None, None
            if features_tag:
                txt = features_tag.get_text(strip=True)
                partes = [p.strip() for p in txt.split("·")]
                if len(partes) >= 1:
                    estado = partes[0]
                if len(partes) >= 2 and "m²" in partes[1]:
                    tamanho = re.sub(r"[^\d]", "", partes[1])
                    tamanho = int(tamanho) if tamanho else None

            # Tipologia
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

            # Preço atual / antigo / desconto
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

            # Preço por metro
            preco_por_metro = None
            if preco and tamanho:
                preco_por_metro = round(preco / tamanho, 2)

            propriedades_lista.append({
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
                "data_publicacao": data_pub
            })

    # 3) Escrita em JSON
    date_format = date.today().strftime("%Y_%m_%d")
    file_name = f'ficheiros/propriedades_sapo_{concelho}_{date_format}.json'
    logging.info(f"Salvando dados em {file_name}")
    with open(file_name, "w", encoding="utf-8") as json_file:
        json.dump(propriedades_lista, json_file, ensure_ascii=False, indent=2)
    logging.info(f"{file_name} salvo com sucesso")



###### SQL ######
def json_to_sqlite(json_file, db_file="propriedades.db", table_name="propriedades"):
    """
    Lê um ficheiro JSON e insere os dados numa tabela SQLite.
    Divide a coluna 'localizacao' em freguesia, concelho e distrito.
    """
    logging.info(f"Lendo ficheiro {json_file}")
    with open(json_file, "r", encoding="utf-8") as f:
        data = json.load(f)

    df = pd.DataFrame(data)

    # Criar novas colunas a partir de 'localizacao'
    if "localizacao" in df.columns:
        freguesias, concelhos, distritos = [], [], []
        for loc in df["localizacao"].fillna(""):
            partes = [p.strip() for p in loc.split(",")]
            freguesia, concelho, distrito = None, None, None

            if len(partes) >= 1:
                freguesia = partes[0]
            if len(partes) >= 2:
                concelho = partes[1]
            if len(partes) >= 3:
                distrito = partes[2].replace("Distrito de ", "").strip()

            freguesias.append(freguesia)
            concelhos.append(concelho)
            distritos.append(distrito)

        df["freguesia"] = freguesias
        df["concelho"] = concelhos
        df["distrito"] = distritos

    logging.info(f"Inserindo {len(df)} registos na base {db_file}, tabela {table_name}")

    conn = sqlite3.connect(db_file)
    df.to_sql(table_name, conn, if_exists="replace", index=False)
    conn.close()

    logging.info("Dados inseridos com sucesso!")

def query_sqlite(db_file="propriedades.db", query="SELECT * FROM propriedades LIMIT 5"):
    conn = sqlite3.connect(db_file)
    df = pd.read_sql_query(query, conn)
    conn.close()
    return df

# scrape_sapo_site()

# df = json_to_table('ficheiros/propriedades_sapo_amadora_2025_09_21.json')

# Inserir JSON na base
# json_to_sqlite("ficheiros/propriedades_sapo_amadora_2025_09_21.json")

# Consultar registos por localicao  
df = query_sqlite("propriedades.db", "SELECT freguesia,count(*) FROM propriedades group by freguesia order by count(*) desc")
print(df)
