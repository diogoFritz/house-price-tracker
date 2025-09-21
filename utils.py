import json
import pandas as pd
import requests
from bs4 import BeautifulSoup
from models.Apartamento import Apartamento   # importa a classe
import logging
from datetime import date
import re
import math

# Função para converter JSON em formato formato CSV
def json_to_csv(json_file, csv_path):
    df = json_to_table(json_file)
    df.to_csv(csv_path, index=False, encoding="utf-8")
    print(f"Arquivo CSV criado: {csv_path}")

# Função para converter JSON em formato tabular (DataFrame)
def json_to_table(json_file):
    with open(json_file, "r", encoding="utf-8") as f:
        data = json.load(f)
    
    # Se o JSON for apenas um objeto, transformamos numa lista
    if isinstance(data, dict):
        data = [data]
    
    df = pd.DataFrame(data)
    return df

# Função para converter DataFrame em CSV
def table_to_csv(df, csv_file):
    df.to_csv(csv_file, index=False, encoding="utf-8")
    print(f"Arquivo CSV criado: {csv_file}")

# Exemplo de uso
# if __name__ == "__main__":
#     # Lê o JSON e converte para tabela
#     df = json_to_table("schema_apartamento.json")  # <-- substitui pelo nome do teu ficheiro
#     print(df)

# Exporta para CSV
# table_to_csv(df, "ficheiros/imovel.csv")
