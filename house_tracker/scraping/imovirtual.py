import json
import re

import requests
from bs4 import BeautifulSoup

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
