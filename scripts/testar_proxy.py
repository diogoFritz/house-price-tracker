"""Teste rápido: confirma que o proxy (IDEALISTA_PROXY_URI) está a funcionar,
sem envolver Selenium — só um pedido HTTP simples através dele."""
import os

import requests
from dotenv import load_dotenv

load_dotenv()

uri = os.environ.get("IDEALISTA_PROXY_URI")
if not uri:
    print("ERRO: IDEALISTA_PROXY_URI não está definida (verifica o .env).")
else:
    proxies = {"http": f"http://{uri}", "https": f"http://{uri}"}
    try:
        r = requests.get("https://httpbin.org/ip", proxies=proxies, timeout=20)
        print("SUCESSO. Resposta:", r.text)
    except Exception as e:
        print(f"ERRO ao usar o proxy: {e}")
