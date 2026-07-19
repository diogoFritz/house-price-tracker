"""Scraper para o Idealista.

O Idealista usa DataDome (proteção anti-bot) que bloqueia pedidos simples e
por vezes desafia mesmo pedidos feitos com um browser real (Selenium) com um
CAPTCHA interativo. Este módulo pode resolver esse CAPTCHA via CapSolver
(omissão, ver CAPTCHA_PROVIDER) ou via 2Captcha.

Configuração necessária (variáveis de ambiente — define-as num ficheiro ".env"
na raiz do projeto, copiando ".env.example"; nunca hardcoded no código):
    CAPSOLVER_API_KEY    - API key da conta CapSolver (provider por omissão)
    TWOCAPTCHA_API_KEY   - API key da conta 2Captcha (provider alternativo)
    IDEALISTA_PROXY_URI  - "login:password@ip:porta" de um proxy
    IDEALISTA_PROXY_TYPE - tipo do proxy (default: "HTTPS")

Nota: mesmo resolvendo o CAPTCHA, o DataDome pode voltar a desafiar pedidos
seguintes com alguma frequência — cada resolução tem custo (o serviço de
CAPTCHA cobra por resolução).
"""
import contextlib
import functools
import json
import logging
import os
import ssl
import tempfile
import time

import requests
import undetected_chromedriver as uc
from dotenv import load_dotenv
from selenium.common.exceptions import WebDriverException
from selenium.webdriver.common.by import By
from tqdm import tqdm
from twocaptcha import TwoCaptcha

load_dotenv()
requests.packages.urllib3.disable_warnings(requests.packages.urllib3.exceptions.InsecureRequestWarning)

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

# Tem de ser um dos userAgent suportados pelo CapSolver para DatadomeSliderTask
# (Chrome 137 a 149, Windows) — ver https://docs.capsolver.com/en/guide/captcha/datadome/.
# É também o UA real usado pelo browser (ver _new_driver), por isso os dois
# lados (browser e CapSolver) ficam sempre consistentes.
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/149.0.0.0 Safari/537.36"
)


def _get_solver():
    api_key = os.environ.get("TWOCAPTCHA_API_KEY")
    if not api_key:
        raise RuntimeError(
            "Define a variável de ambiente TWOCAPTCHA_API_KEY antes de usar este módulo."
        )
    return TwoCaptcha(api_key)


@contextlib.contextmanager
def _sem_verificacao_ssl():
    """A biblioteca 2captcha-python usa requests.post/get sem expor 'verify=False'.
    2captcha.com (tal como o idealista.pt) não envia a cadeia de certificados
    completa, o que faz o requests falhar a verificação SSL por omissão — por
    isso, tal como o resto do projeto, desativamos a verificação só durante a
    chamada ao 2Captcha."""
    original_post, original_get = requests.post, requests.get
    requests.post = functools.partial(original_post, verify=False)
    requests.get = functools.partial(original_get, verify=False)
    try:
        yield
    finally:
        requests.post, requests.get = original_post, original_get


@contextlib.contextmanager
def _sem_verificacao_ssl_urllib():
    """O undetected-chromedriver usa urllib (não requests) para consultar a versão
    mais recente do chromedriver a descarregar — mesmo problema de certificados
    deste PC, mas noutra biblioteca, por isso precisa do seu próprio contorno."""
    original_context = ssl._create_default_https_context
    ssl._create_default_https_context = ssl._create_unverified_context
    try:
        yield
    finally:
        ssl._create_default_https_context = original_context


def _parse_proxy_uri(uri):
    auth, hostport = uri.split("@", 1)
    user, senha = auth.split(":", 1)
    host, porta = hostport.split(":", 1)
    return host, int(porta), user, senha


def _get_proxy():
    uri = os.environ.get("IDEALISTA_PROXY_URI")
    if not uri:
        raise RuntimeError(
            "Define a variável de ambiente IDEALISTA_PROXY_URI (login:password@ip:porta)."
        )
    return {"type": os.environ.get("IDEALISTA_PROXY_TYPE", "HTTPS"), "uri": uri}


def _build_proxy_auth_extension(host, porta, user, senha):
    """Cria uma extensão Chrome temporária que autentica automaticamente no proxy.

    O Chrome não suporta autenticação de proxy (utilizador/password) por linha de
    comandos — abre sempre um popup. Esta extensão intercepta o pedido de
    autenticação (webRequest.onAuthRequired) e responde com as credenciais.
    """
    ext_dir = tempfile.mkdtemp(prefix="idealista_proxy_ext_")

    manifest = {
        "version": "1.0.0",
        "manifest_version": 2,
        "name": "Proxy Auth",
        "permissions": [
            "proxy", "tabs", "unlimitedStorage", "storage",
            "<all_urls>", "webRequest", "webRequestBlocking",
        ],
        "background": {"scripts": ["background.js"]},
        "minimum_chrome_version": "22.0.0",
    }
    with open(os.path.join(ext_dir, "manifest.json"), "w", encoding="utf-8") as f:
        json.dump(manifest, f)

    background_js = f"""
    var config = {{
        mode: "fixed_servers",
        rules: {{
            singleProxy: {{ scheme: "http", host: "{host}", port: parseInt({porta}) }},
            bypassList: ["localhost"]
        }}
    }};
    chrome.proxy.settings.set({{value: config, scope: "regular"}}, function() {{}});

    chrome.webRequest.onAuthRequired.addListener(
        function(details) {{
            return {{authCredentials: {{username: "{user}", password: "{senha}"}}}};
        }},
        {{urls: ["<all_urls>"]}},
        ["blocking"]
    );
    """
    with open(os.path.join(ext_dir, "background.js"), "w", encoding="utf-8") as f:
        f.write(background_js)

    return ext_dir


def _chrome_major_version():
    """Deteta a versão principal do Chrome instalado (via registo do Windows), para
    o undetected-chromedriver descarregar o chromedriver certo — sem isto, tenta
    sempre a versão mais recente, que pode não corresponder ao Chrome instalado
    (ex.: chromedriver 151 vs Chrome 150) e falha a criar sessão."""
    import re
    import subprocess
    import winreg

    try:
        with winreg.OpenKey(
            winreg.HKEY_LOCAL_MACHINE,
            r"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\chrome.exe",
        ) as key:
            chrome_path = winreg.QueryValue(key, None)
        # "chrome.exe --version" abre uma janela normal do browser em vez de
        # imprimir a versão e sair (ao contrário do Linux) — lê-se antes a versão
        # dos metadados do ficheiro via PowerShell.
        saida = subprocess.check_output(
            ["powershell", "-NoProfile", "-Command",
             f"(Get-Item '{chrome_path}').VersionInfo.ProductVersion"],
            text=True,
        )
        match = re.search(r"(\d+)\.", saida)
        return int(match.group(1)) if match else None
    except (OSError, subprocess.SubprocessError):
        return None


def _new_driver(usar_proxy=False):
    """Usa undetected-chromedriver em vez do Selenium normal: o Idealista estava a
    detetar o Chrome headless comum (via fingerprint, não só navigator.webdriver) e
    a devolver silenciosamente a homepage em vez dos resultados, mesmo sem mostrar
    CAPTCHA. O undetected-chromedriver aplica os patches anti-deteção internamente."""
    options = uc.ChromeOptions()
    options.add_argument(f"user-agent={USER_AGENT}")

    # Não precisamos das fotos dos imóveis, só do texto/HTML — bloquear imagens
    # poupa a grande maioria do tráfego que passa pelo proxy (pago por GB).
    options.add_experimental_option(
        "prefs", {"profile.managed_default_content_settings.images": 2}
    )
    options.add_argument("--blink-settings=imagesEnabled=false")

    if usar_proxy:
        uri = os.environ.get("IDEALISTA_PROXY_URI")
        if not uri:
            raise RuntimeError(
                "Define a variável de ambiente IDEALISTA_PROXY_URI (login:password@ip:porta)."
            )
        host, porta, user, senha = _parse_proxy_uri(uri)
        ext_dir = _build_proxy_auth_extension(host, porta, user, senha)
        options.add_argument(f"--load-extension={ext_dir}")

    with _sem_verificacao_ssl_urllib():
        return uc.Chrome(options=options, headless=True, version_main=_chrome_major_version())


def _find_captcha_iframe(driver):
    try:
        return driver.find_element(By.CSS_SELECTOR, "iframe[src*='captcha-delivery.com']")
    except Exception:
        return None


# Tempo máximo de espera pela resolução do CAPTCHA (default da lib 2captcha é 120s).
# 30s mostrou-se insuficiente na prática para um DataDome ser resolvido.
CAPTCHA_TIMEOUT_S = 120

# Serviço de resolução de CAPTCHA a usar por omissão. O 2Captcha falhou
# repetidamente (ERROR_CAPTCHA_UNSOLVABLE) neste DataDome específico do
# idealista.pt — o CapSolver tem um tipo de tarefa dedicado a DataDome
# (DatadomeSliderTask), por isso é a opção por omissão agora.
CAPTCHA_PROVIDER = "capsolver"


def _solve_captcha(driver, url, captcha_url, provider=CAPTCHA_PROVIDER):
    """Resolve o CAPTCHA DataDome e injeta o cookie resultante na sessão do driver."""
    if provider == "capsolver":
        cookie_str = _resolver_capsolver(url, captcha_url)
    elif provider == "2captcha":
        cookie_str = _resolver_2captcha(url, captcha_url)
    else:
        raise ValueError(f"provider desconhecido: {provider!r} (usa 'capsolver' ou '2captcha')")

    nome, resto = cookie_str.split("=", 1)
    valor = resto.split(";", 1)[0]

    driver.add_cookie({"name": nome.strip(), "value": valor.strip(), "domain": ".idealista.pt"})
    logging.info(f"[Idealista] Cookie do CAPTCHA resolvido via {provider} e aplicado.")


def _resolver_2captcha(url, captcha_url):
    solver = _get_solver()
    proxy = _get_proxy()

    logging.info(f"[Idealista] A enviar CAPTCHA para o 2Captcha (timeout {CAPTCHA_TIMEOUT_S}s)...")
    with _sem_verificacao_ssl():
        result = solver.datadome(
            captcha_url=captcha_url,
            pageurl=url,
            userAgent=USER_AGENT,
            proxy=proxy,
            timeout=CAPTCHA_TIMEOUT_S,
        )
    return result["code"]  # formato: "datadome=VALOR; Path=/; Secure; SameSite=Lax"


# Intervalo entre consultas ao getTaskResult do CapSolver, enquanto o CAPTCHA
# ainda está a ser resolvido.
CAPSOLVER_POLL_INTERVAL_S = 3


def _resolver_capsolver(url, captcha_url):
    """Resolve o CAPTCHA DataDome via CapSolver (tarefa DatadomeSliderTask) e
    devolve a string do cookie "datadome=VALOR; ...", tal como a API do 2Captcha."""
    api_key = os.environ.get("CAPSOLVER_API_KEY")
    if not api_key:
        raise RuntimeError("Define a variável de ambiente CAPSOLVER_API_KEY antes de usar este módulo.")

    uri = os.environ.get("IDEALISTA_PROXY_URI")
    if not uri:
        raise RuntimeError("Define a variável de ambiente IDEALISTA_PROXY_URI (login:password@ip:porta).")
    host, porta, user, senha = _parse_proxy_uri(uri)

    logging.info(f"[Idealista] A enviar CAPTCHA para o CapSolver (timeout {CAPTCHA_TIMEOUT_S}s)...")
    criar = requests.post(
        "https://api.capsolver.com/createTask",
        json={
            "clientKey": api_key,
            "task": {
                "type": "DatadomeSliderTask",
                "websiteURL": url,
                "captchaUrl": captcha_url,
                "userAgent": USER_AGENT,
                "proxy": f"{host}:{porta}:{user}:{senha}",
            },
        },
        timeout=30,
        verify=False,
    )
    if criar.status_code != 200:
        raise RuntimeError(f"CapSolver createTask devolveu {criar.status_code}: {criar.text}")
    resposta = criar.json()
    if resposta.get("errorId"):
        raise RuntimeError(f"CapSolver createTask falhou: {resposta.get('errorDescription')}")
    task_id = resposta["taskId"]

    inicio = time.time()
    while time.time() - inicio < CAPTCHA_TIMEOUT_S:
        time.sleep(CAPSOLVER_POLL_INTERVAL_S)
        resultado = requests.post(
            "https://api.capsolver.com/getTaskResult",
            json={"clientKey": api_key, "taskId": task_id},
            timeout=30,
            verify=False,
        )
        resultado.raise_for_status()
        dados = resultado.json()
        if dados.get("errorId"):
            raise RuntimeError(f"CapSolver getTaskResult falhou: {dados.get('errorDescription')}")
        if dados.get("status") == "ready":
            return dados["solution"]["cookie"]
        if dados.get("status") == "failed":
            raise RuntimeError("CapSolver devolveu status 'failed'.")

    raise TimeoutError(f"CapSolver não respondeu em {CAPTCHA_TIMEOUT_S}s.")


def _dismiss_cookie_banner(driver):
    """Fecha o banner de consentimento de cookies (Didomi) — a app não renderiza
    o conteúdo real da página enquanto o banner estiver por resolver."""
    try:
        botao = driver.find_element(By.ID, "didomi-notice-agree-button")
        botao.click()
        time.sleep(1)
        logging.info("[Idealista] Banner de cookies fechado.")
    except Exception:
        pass


class CaptchaFalhouError(Exception):
    """O CAPTCHA apareceu mas não foi possível resolvê-lo (2Captcha falhou ou esgotaram-se as tentativas)."""


class PaginaGenericaError(Exception):
    """O DataDome serviu a homepage genérica em vez da página pedida, sem mostrar
    CAPTCHA — um bloqueio silencioso ("chamariz") que não dá hipótese de resolver."""


# Título da homepage genérica que o DataDome serve como "chamariz" quando decide
# bloquear silenciosamente em vez de desafiar com CAPTCHA.
_HOMEPAGE_TITLE = "www.idealista.pt — Casas venda. Casas arrendar. Apartamentos. Moradias — idealista"


def _aquecer_sessao(driver):
    """Visita a homepage antes da página de resultados, para parecer navegação
    humana em vez de um pedido direto a um link profundo (deep link) — um dos
    sinais que o DataDome usa para decidir se serve a homepage "chamariz"."""
    driver.get("https://www.idealista.pt/")
    time.sleep(4)
    _dismiss_cookie_banner(driver)
    driver.execute_script("window.scrollBy(0, 400);")
    time.sleep(2)
    driver.execute_script("window.scrollBy(0, 400);")
    time.sleep(3)


def fetch_page(url, aquecer=True):
    """Vai buscar uma página do Idealista, resolvendo o CAPTCHA DataDome via 2Captcha se aparecer.

    Sem retries: se o CAPTCHA aparecer e o 2Captcha não conseguir resolvê-lo à
    primeira, desiste logo (lança CaptchaFalhouError). Se o DataDome servir a
    homepage genérica sem CAPTCHA, lança PaginaGenericaError (não há nada para
    resolver, é um bloqueio silencioso). Erros de rede/browser propagam
    normalmente (WebDriverException, etc.) — quem chama esta função decide como
    lidar com eles (ver fetch_all_pages).
    """
    inicio = time.time()
    driver = _new_driver(usar_proxy=True)
    try:
        if aquecer:
            _aquecer_sessao(driver)

        driver.get(url)
        time.sleep(3)
        _dismiss_cookie_banner(driver)

        iframe = _find_captcha_iframe(driver)
        if iframe is None:
            duracao = time.time() - inicio
            if driver.title.strip() == _HOMEPAGE_TITLE and url != "https://www.idealista.pt/":
                raise PaginaGenericaError(
                    f"DataDome serviu a homepage genérica em vez de {url} ({duracao:.1f}s decorridos)."
                )
            logging.info(f"[Idealista] Sem CAPTCHA, página carregada normalmente ({duracao:.1f}s).")
            return driver.page_source

        logging.warning(f"[Idealista] CAPTCHA detetado, {time.time() - inicio:.1f}s decorridos.")
        captcha_url = iframe.get_attribute("src")
        try:
            _solve_captcha(driver, url, captcha_url)
        except Exception as e:
            # Qualquer falha do 2Captcha (ApiException, TimeoutException, etc.)
            # conta como CAPTCHA não resolvido — sem retry, desiste já.
            raise CaptchaFalhouError(
                f"2Captcha não conseguiu resolver ({time.time() - inicio:.1f}s decorridos): {e}"
            ) from e

        driver.get(url)
        time.sleep(3)
        _dismiss_cookie_banner(driver)
        return driver.page_source
    finally:
        driver.quit()


# Caminho do concelho na URL — por omissão é só "{concelho}/", mas Lisboa (onde
# o concelho tem o mesmo nome do distrito) precisa do caminho duplicado
# "lisboa/lisboa/". Vão-se acrescentando exceções à medida que se confirmam.
_CONCELHO_PATH = {
    "lisboa": "lisboa/lisboa",
}


def _url_pagina(concelho, page):
    caminho = _CONCELHO_PATH.get(concelho, concelho)
    base = f"https://www.idealista.pt/comprar-casas/{caminho}/"
    if page == 1:
        return base
    return f"{base}pagina-{page}.htm"


def fetch_all_pages(concelho="lisboa", max_paginas=20, pasta_saida="idealista/html"):
    """Percorre as páginas de resultados de um concelho, gravando o HTML de cada
    página com sucesso. Páginas que falham (CAPTCHA não resolvido, erro de rede,
    bloqueio, etc.) são saltadas de imediato, sem retry — sem interromper a
    extração das restantes — com a justificação registada no resumo final.

    Retomável: se já houver uma página gravada em disco (sucesso de uma corrida
    anterior), é reaproveitada em vez de repedida — cada pedido tem custo real
    (2Captcha + tráfego do proxy), por isso não vale a pena repetir o que já
    funcionou.

    Devolve um dict: {"sucesso": [...], "falha_captcha": [...], "falha_outra": [...]}
    """
    concelho_dir = os.path.join(pasta_saida, concelho)
    os.makedirs(concelho_dir, exist_ok=True)

    resultado = {"sucesso": [], "falha_captcha": [], "falha_generica": [], "falha_outra": []}
    barra = tqdm(range(1, max_paginas + 1), desc=f"Idealista [{concelho}]", unit="página")

    for page in barra:
        filename = os.path.join(concelho_dir, f"pagina{page}.html")
        if os.path.exists(filename):
            resultado["sucesso"].append(page)
            barra.set_postfix_str(f"página {page} em cache")
            continue

        url = _url_pagina(concelho, page)
        inicio_pagina = time.time()
        html = None
        motivo_falha = None

        try:
            html = fetch_page(url)
        except CaptchaFalhouError as e:
            motivo_falha = ("captcha", str(e))
            tqdm.write(f"[Idealista] Página {page}: CAPTCHA — {e}")
        except PaginaGenericaError as e:
            motivo_falha = ("generica", str(e))
            tqdm.write(f"[Idealista] Página {page}: homepage genérica (bloqueio silencioso) — {e}")
        except WebDriverException as e:
            motivo_falha = ("rede/browser", str(e).splitlines()[0])
            tqdm.write(f"[Idealista] Página {page}: erro de rede/browser — {motivo_falha[1]}")

        duracao = time.time() - inicio_pagina

        if html is not None:
            with open(filename, "w", encoding="utf-8") as f:
                f.write(html)
            resultado["sucesso"].append(page)
            barra.set_postfix_str(f"pág {page} OK, {duracao:.1f}s")
        elif motivo_falha and motivo_falha[0] == "captcha":
            resultado["falha_captcha"].append({"pagina": page, "motivo": motivo_falha[1], "duracao_s": round(duracao, 1)})
            barra.set_postfix_str(f"pág {page} CAPTCHA (saltada), {duracao:.1f}s")
        elif motivo_falha and motivo_falha[0] == "generica":
            resultado["falha_generica"].append({"pagina": page, "motivo": motivo_falha[1], "duracao_s": round(duracao, 1)})
            barra.set_postfix_str(f"pág {page} homepage genérica (saltada), {duracao:.1f}s")
        else:
            motivo_txt = motivo_falha[1] if motivo_falha else "desconhecido"
            resultado["falha_outra"].append({"pagina": page, "motivo": motivo_txt, "duracao_s": round(duracao, 1)})
            barra.set_postfix_str(f"pág {page} falhou (saltada), {duracao:.1f}s")

    barra.close()

    total = len(resultado["sucesso"]) + len(resultado["falha_captcha"]) + len(resultado["falha_generica"]) + len(resultado["falha_outra"])
    logging.info(
        f"[Idealista] Resumo {concelho}: {len(resultado['sucesso'])}/{total} sucesso, "
        f"{len(resultado['falha_captcha'])} falharam por CAPTCHA, "
        f"{len(resultado['falha_generica'])} devolveram homepage genérica, "
        f"{len(resultado['falha_outra'])} falharam por outro motivo."
    )
    for f in resultado["falha_captcha"]:
        logging.info(f"  - página {f['pagina']}: CAPTCHA — {f['motivo']} ({f['duracao_s']}s)")
    for f in resultado["falha_generica"]:
        logging.info(f"  - página {f['pagina']}: homepage genérica ({f['duracao_s']}s)")
    for f in resultado["falha_outra"]:
        logging.info(f"  - página {f['pagina']}: {f['motivo']} ({f['duracao_s']}s)")

    return resultado
