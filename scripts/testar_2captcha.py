"""Teste rápido: confirma que a API key do 2Captcha é válida, consultando o saldo da conta."""
import os

from dotenv import load_dotenv
from twocaptcha import TwoCaptcha

load_dotenv()

api_key = os.environ.get("TWOCAPTCHA_API_KEY")
if not api_key:
    print("ERRO: a variável de ambiente TWOCAPTCHA_API_KEY não está definida nesta sessão.")
else:
    solver = TwoCaptcha(api_key)
    try:
        saldo = solver.balance()
        print(f"Key válida. Saldo da conta: ${saldo}")
    except Exception as e:
        print(f"ERRO ao validar a key: {e}")
