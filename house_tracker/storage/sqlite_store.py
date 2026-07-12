import json
import logging
import sqlite3

import pandas as pd


def json_to_sqlite(json_file, db_file="data/propriedades.db", table_name="propriedades"):
    """
    Lê um ficheiro JSON e insere os dados numa tabela SQLite.
    Divide a coluna 'localizacao' em freguesia, concelho e distrito.
    """
    logging.info(f"Lendo ficheiro {json_file}")
    with open(json_file, "r", encoding="utf-8") as f:
        data = json.load(f)

    df = pd.DataFrame(data)

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


def query_sqlite(db_file="data/propriedades.db", query="SELECT * FROM propriedades LIMIT 5"):
    conn = sqlite3.connect(db_file)
    df = pd.read_sql_query(query, conn)
    conn.close()
    return df
