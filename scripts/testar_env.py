"""Diagnóstico: mostra exatamente como a IDEALISTA_PROXY_URI está a ser lida e
interpretada, sem revelar a password."""
import os

from dotenv import load_dotenv

load_dotenv()

uri = os.environ.get("IDEALISTA_PROXY_URI")
print("valor bruto (repr, mostra espaços/aspas escondidos):", repr(uri))

if uri:
    try:
        auth, hostport = uri.split("@", 1)
        user, senha = auth.split(":", 1)
        host, porta = hostport.split(":", 1)
        print(f"user:  {user!r}")
        print(f"senha: {'*' * len(senha)} ({len(senha)} caracteres)")
        print(f"host:  {host!r}")
        print(f"porta: {porta!r}")
    except ValueError as e:
        print(f"ERRO ao separar a URI: {e}")
