import json
import logging
from pathlib import Path

import pandas as pd


def json_to_table(json_file):
    """Lê um ficheiro JSON de propriedades e devolve um DataFrame tabular."""
    logging.info(f"Lendo ficheiro {json_file}")
    with open(json_file, "r", encoding="utf-8") as f:
        data = json.load(f)

    if isinstance(data, dict):
        data = [data]

    df = pd.DataFrame(data)
    logging.info(f"DataFrame criado com {len(df)} registos e {len(df.columns)} colunas")
    return df


def table_to_csv(df, csv_file):
    Path(csv_file).parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(csv_file, index=False, encoding="utf-8-sig")
    logging.info(f"Tabela salva em {csv_file}")


def json_to_csv(json_file, csv_file):
    df = json_to_table(json_file)
    table_to_csv(df, csv_file)
    return df
