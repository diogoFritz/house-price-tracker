"""Gera docs/idealista_data.json — os dados da página de análise do Idealista
(docs/idealista.html), com navegação por níveis Distrito › Concelho › Freguesia.

O Idealista é a única fonte com descrição completa por anúncio, o que permite
detetar "situações especiais" (usufruto, herança, obras, arrendado, permuta,
vendedor motivado) e descidas de preço.

Estrutura produzida:
  - listings: recolha MAIS RECENTE (mercado atual), um registo por anúncio;
  - concelhos / freguesias: resumo por nível (contagem, €/m² e preço medianos);
  - history: histórico de €/m² mediano por nível (distrito/concelho/freguesia),
    ao longo de TODAS as recolhas do Idealista. Só-Idealista, por isso hoje é
    um único ponto; cresce a cada nova extração.

Uso: python scripts/build_idealista_data.py
"""
import json
import re
import sys
from datetime import datetime
from pathlib import Path
from collections import defaultdict
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from house_tracker.scraping.classificacao import categorias as detetar_categorias

ROOT = Path(__file__).resolve().parent.parent
IDEALISTA_DIR = ROOT / "idealista" / "json"

MIN_PPM, MAX_PPM = 300, 20000
MIN_TAMANHO = 10
FREG_SEP = "|||"  # separador concelho/freguesia nas chaves de histórico

# Área plausível por tipologia (m²) — descarta erros de recolha (ex. um "T0 de
# 400 m²" que distorceria a média e apareceria como falso "negócio -83%").
TAMANHO_RANGE = {
    "T0": (15, 120), "T1": (25, 160), "T2": (40, 220), "T3": (55, 300),
    "T4": (70, 400), "T5": (90, 500),
}
TAMANHO_RANGE_DEFAULT = (110, 800)  # T6 e acima

CATEGORIAS_LABEL = {
    "usufruto": "Usufruto / nua-propriedade",
    "arrendado": "Arrendado / investimento",
    "obras": "Precisa de obras",
    "heranca": "Herança / partilha",
    "permuta": "Aceita permuta",
    "urgente": "Vendedor motivado",
}

CONCELHO_NOME = {
    "lisboa": "Lisboa", "amadora": "Amadora", "loures": "Loures",
    "odivelas": "Odivelas", "oeiras": "Oeiras", "sintra": "Sintra",
    "alenquer": "Alenquer", "arruda-dos-vinhos": "Arruda dos Vinhos",
    "azambuja": "Azambuja", "cadaval": "Cadaval", "cascais": "Cascais",
    "lourinha": "Lourinhã", "mafra": "Mafra",
    "sobral-de-monte-agraco": "Sobral de Monte Agraço",
    "torres-vedras": "Torres Vedras", "vila-franca-de-xira": "Vila Franca de Xira",
}


def _median(values):
    values = sorted(v for v in values if v is not None)
    if not values:
        return None
    n = len(values)
    mid = n // 2
    return values[mid] if n % 2 else round((values[mid - 1] + values[mid]) / 2, 2)


# Redes/franchises com muitos balcões independentes — agrupam-se todos sob a
# marca, para o filtro por agência não ter centenas de sub-ramos ("RE/MAX
# Vantagem Park", "RE/MAX Expo", ... -> "RE/MAX"). Ordem importa: o primeiro
# padrão que casar ganha. Agências independentes mantêm o nome próprio.
_GRUPOS_AGENCIA = [
    (re.compile(r"^re\s*/?\s*max\b", re.I), "RE/MAX"),
    (re.compile(r"^(century\s*21|c21)\b", re.I), "Century 21"),
    (re.compile(r"^kw\b|keller\s*williams", re.I), "Keller Williams (KW)"),
    (re.compile(r"^era\b", re.I), "ERA"),
    (re.compile(r"^engel\s*&?\s*v", re.I), "Engel & Völkers"),
    (re.compile(r"^zome\b", re.I), "Zome"),
    (re.compile(r"^iad\b", re.I), "IAD"),
    (re.compile(r"^dils\b", re.I), "Dils"),
    (re.compile(r"^jll\b", re.I), "JLL"),
    (re.compile(r"^predimed\b", re.I), "Predimed"),
    (re.compile(r"^remaxgroup|^remax\b", re.I), "RE/MAX"),
]


# Uma agência/rede com menos anúncios do que isto não aparece sozinha no filtro
# — junta-se em "Outras consultoras", para o dropdown ter só os grandes players.
MIN_AGENCIA_PROPRIA = 50
OUTRAS = "Outras consultoras"


def _grupo_agencia(nome):
    # Anúncios sem agência são de particulares — agrupam-se em "Independente".
    if not nome or not nome.strip():
        return "Independente"
    for padrao, marca in _GRUPOS_AGENCIA:
        if padrao.search(nome):
            return marca
    return nome.strip()


def _normaliza_freguesia(fr):
    if not fr:
        return None
    fr = fr.strip()
    m = re.match(r"^.+\(([^()]+)\)\s*$", fr)
    fr = m.group(1).strip() if m else fr
    fr = re.sub(r"^Uni[aã]o(?:\s+das)?\s+freguesias\s+(?:de|do|da|dos|das)?\s*", "", fr, flags=re.IGNORECASE).strip()
    return fr or None


def _valido(r):
    if not r.get("link") or r.get("preco") is None:
        return False
    ppm = r.get("preco_por_metro")
    if ppm is not None and not (MIN_PPM <= ppm <= MAX_PPM):
        return False
    tam = r.get("tamanho")
    if tam is not None:
        if tam < MIN_TAMANHO:
            return False
        if r.get("tipologia"):
            lo, hi = TAMANHO_RANGE.get(r["tipologia"], TAMANHO_RANGE_DEFAULT)
            if not (lo <= tam <= hi):
                return False
    return True


def _ficheiros_por_concelho():
    """{slug: [(data, Path), ...]} dos consolidados <concelho>_<data>.json,
    por concelho e ordenados por data. Cada concelho pode ter datas diferentes
    (ex. Lisboa foi extraída noutro dia por ser dividida por freguesia)."""
    por_concelho = defaultdict(list)
    for f in IDEALISTA_DIR.glob("*_*.json"):
        m = re.match(r"([a-z-]+)_(\d{8})$", f.stem)
        if m and m.group(1) in CONCELHO_NOME:
            por_concelho[m.group(1)].append((m.group(2), f))
    for slug in por_concelho:
        por_concelho[slug].sort()
    return por_concelho


def _carrega(slug, path):
    rows = json.loads(path.read_text(encoding="utf-8"))
    saida = []
    vistos = set()
    for r in rows:
        if not _valido(r) or r["id"] in vistos:
            continue
        vistos.add(r["id"])
        r["concelho"] = CONCELHO_NOME[slug]
        r["freguesia"] = _normaliza_freguesia(r.get("freguesia"))
        saida.append(r)
    return saida


LISTING_FIELDS = [
    "id", "titulo", "link", "tipologia", "tamanho", "preco", "preco_por_metro",
    "preco_antigo", "desconto_pct", "freguesia", "concelho", "agencia", "num_fotos",
]


def _history_por_nivel(por_concelho, data_max, distrito_ppms):
    """Histórico de €/m² mediano por nível.

    Concelho e freguesia usam as datas próprias de cada concelho (crescem a
    cada extração). O distrito é um único ponto do snapshot atual (dated
    data_max) — um histórico de distrito por data só faria sentido com todos
    os concelhos recolhidos no mesmo dia, o que nem sempre acontece (ex. Lisboa
    é extraída à parte)."""
    conc, freg = defaultdict(list), defaultdict(list)
    for slug, ficheiros in por_concelho.items():
        nome = CONCELHO_NOME[slug]
        for data, path in ficheiros:
            rows = _carrega(slug, path)
            conc[nome].append({"date": data, "median_ppm": _median([r["preco_por_metro"] for r in rows]), "count": len(rows)})
            por_f = defaultdict(list)
            for r in rows:
                if r["freguesia"]:
                    por_f[r["freguesia"]].append(r["preco_por_metro"])
            for fr, ppms in por_f.items():
                freg[nome + FREG_SEP + fr].append({"date": data, "median_ppm": _median(ppms), "count": len(ppms)})
    dist = [{"date": data_max, "median_ppm": _median(distrito_ppms), "count": len(distrito_ppms)}]
    return dist, dict(conc), dict(freg)


def main():
    por_concelho_files = _ficheiros_por_concelho()
    if not por_concelho_files:
        print("Sem dados do Idealista em idealista/json/.")
        return
    # Ficheiro MAIS RECENTE de cada concelho (as datas podem diferir entre
    # concelhos) -> os anúncios mostrados na página.
    data_max = max(fs[-1][0] for fs in por_concelho_files.values())

    listings = []
    for slug, ficheiros in por_concelho_files.items():
        _data, path = ficheiros[-1]
        for r in _carrega(slug, path):
            item = {k: r.get(k) for k in LISTING_FIELDS}
            item["categorias"] = detetar_categorias(r.get("descricao") or r.get("titulo") or "") or []
            item["grupo_agencia"] = _grupo_agencia(r.get("agencia"))
            listings.append(item)

    # Junta as agências pequenas em "Outras consultoras" — o filtro fica só com
    # os grandes players + Independente + Outras. A tabela mantém o nome real.
    tamanho_grupo = defaultdict(int)
    for it in listings:
        tamanho_grupo[it["grupo_agencia"]] += 1
    for it in listings:
        g = it["grupo_agencia"]
        if g != "Independente" and tamanho_grupo[g] < MIN_AGENCIA_PROPRIA:
            it["grupo_agencia"] = OUTRAS

    por_concelho = defaultdict(list)
    for it in listings:
        por_concelho[it["concelho"]].append(it)
    concelhos = []
    for nome, sub in por_concelho.items():
        concelhos.append({
            "concelho": nome, "count": len(sub),
            "median_ppm": _median([s["preco_por_metro"] for s in sub]),
            "median_preco": _median([s["preco"] for s in sub]),
        })
    concelhos.sort(key=lambda x: x["median_ppm"] or 0, reverse=True)

    freguesias = defaultdict(list)
    por_freg = defaultdict(list)
    for it in listings:
        if it["freguesia"]:
            por_freg[(it["concelho"], it["freguesia"])].append(it)
    for (c, fr), sub in por_freg.items():
        freguesias[c].append({
            "freguesia": fr, "count": len(sub),
            "median_ppm": _median([s["preco_por_metro"] for s in sub]),
            "median_preco": _median([s["preco"] for s in sub]),
        })
    for c in freguesias:
        freguesias[c].sort(key=lambda x: x["median_ppm"] or 0, reverse=True)

    hist_dist, hist_conc, hist_freg = _history_por_nivel(
        por_concelho_files, data_max, [it["preco_por_metro"] for it in listings])

    cat_counts = defaultdict(int)
    for it in listings:
        for c in it["categorias"]:
            cat_counts[c] += 1
    com_desconto = [it for it in listings if it.get("desconto_pct") is not None]

    overview = {
        "total": len(listings),
        "concelhos": len(por_concelho),
        "median_preco": _median([it["preco"] for it in listings]),
        "median_ppm": _median([it["preco_por_metro"] for it in listings]),
        "n_situacoes": sum(1 for it in listings if it["categorias"]),
        "n_com_desconto": len(com_desconto),
        "n_recolhas": len({d for fs in por_concelho_files.values() for d, _ in fs}),
        "date_max": data_max,
        "generated_at": datetime.now(ZoneInfo("Europe/Lisbon")).strftime("%Y-%m-%dT%H:%M"),
    }

    out = {
        "overview": overview,
        "categorias_label": CATEGORIAS_LABEL,
        "categorias_count": dict(cat_counts),
        "concelhos": concelhos,
        "freguesias": freguesias,
        "history": {"distrito": hist_dist, "concelho": hist_conc, "freguesia": hist_freg},
        "listings": listings,
    }
    out_path = ROOT / "docs" / "idealista_data.json"
    out_path.write_text(json.dumps(out, ensure_ascii=False), encoding="utf-8")
    print(f"{len(listings)} anúncios (recolha {data_max}) -> {out_path}")
    print(f"  {len(por_concelho)} concelhos · {sum(len(v) for v in freguesias.values())} freguesias · "
          f"{overview['n_situacoes']} situações especiais · {overview['n_recolhas']} recolha(s) no histórico")


if __name__ == "__main__":
    main()
