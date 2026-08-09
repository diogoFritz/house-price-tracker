"""Gera docs/idealista_data.json — os dados da página de análise do Idealista
(docs/idealista.html), com navegação Portugal › Distrito › Concelho › Freguesia.

O Idealista é a única fonte com descrição completa por anúncio, o que permite
detetar "situações especiais" (usufruto, herança, obras, arrendado, permuta,
vendedor motivado) e descidas de preço.

Os concelhos e a que distrito pertencem vêm de docs/geo/concelhos_index.json
(gerado da CAOP). Só entram os concelhos que já foram extraídos.

Uso: python scripts/build_idealista_data.py
"""
import json
import re
import sys
import unicodedata
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
FREG_SEP = "|||"

TAMANHO_RANGE = {
    "T0": (15, 120), "T1": (25, 160), "T2": (40, 220), "T3": (55, 300),
    "T4": (70, 400), "T5": (90, 500),
}
TAMANHO_RANGE_DEFAULT = (110, 800)

CATEGORIAS_LABEL = {
    "usufruto": "Usufruto / nua-propriedade",
    "arrendado": "Arrendado / investimento",
    "obras": "Precisa de obras",
    "heranca": "Herança / partilha",
    "permuta": "Aceita permuta",
    "urgente": "Vendedor motivado",
}

# slug -> {nome, distrito, distrito_slug}, gerado da CAOP.
_INDEX = json.loads((ROOT / "docs" / "geo" / "concelhos_index.json").read_text(encoding="utf-8"))
CONCELHO_NOME = {slug: v["nome"] for slug, v in _INDEX.items()}
CONCELHO_DIST = {slug: v["distrito"] for slug, v in _INDEX.items()}


def _median(values):
    values = sorted(v for v in values if v is not None)
    if not values:
        return None
    n = len(values)
    mid = n // 2
    return values[mid] if n % 2 else round((values[mid - 1] + values[mid]) / 2, 2)


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
MIN_AGENCIA_PROPRIA = 50
OUTRAS = "Outras consultoras"


def _grupo_agencia(nome):
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


def _sa(s):  # sem acentos, minúsculas
    return "".join(c for c in unicodedata.normalize("NFD", s or "") if unicodedata.category(c) != "Mn").lower().strip()


_STOP = {"de", "do", "da", "dos", "das", "e", "a", "o", "as", "os"}


def _tokens(fr):
    # Todas as palavras significativas do nome, sem o boilerplate "União das
    # freguesias de" e SEM extrair só os parênteses — nas uniões CAOP o nome da
    # sede vem antes dos parênteses (ex. "...Setúbal (São Julião, ...)"), por
    # isso é preciso manter "setubal" para casar com o "Setúbal" do Idealista.
    x = re.sub(r"uni[aã]o\s+d[ae]s?\s+freguesias?\s+(?:de|do|da|dos|das)?\s*", " ", _sa(fr))
    return {t for t in re.findall(r"[a-z]+", x) if t not in _STOP and len(t) >= 3}


# Nomes oficiais de freguesia por concelho (da CAOP, nos geojson gerados) — o
# Idealista usa nomes informais/parciais ("Torres Vedras e Matacães" em vez de
# "Santa Maria, São Pedro e Matacães"; "a dos Cunhados" em vez de "A dos
# Cunhados e Maceira"), por isso reconciliam-se para o nome oficial, para o
# mapa e os dados casarem e as freguesias fundidas somarem juntas.
_NOME2SLUG = {v["nome"]: slug for slug, v in _INDEX.items()}


def _carrega_caop_fregs():
    out = {}
    for nome, sl in _NOME2SLUG.items():
        p = ROOT / "docs" / "geo" / "freguesias" / f"{sl}.json"
        if p.exists():
            names = [f["properties"]["freguesia"] for f in json.loads(p.read_text(encoding="utf-8"))["features"]]
            out[nome] = [(n, _tokens(n)) for n in names]
    return out


_CAOP_FREG = _carrega_caop_fregs()


def _reconcilia_freg(concelho, freg):
    if not freg:
        return freg
    cands = _CAOP_FREG.get(concelho)
    if not cands:
        return freg
    alvo = _sa(_normaliza_freguesia(freg))
    for n, _ in cands:
        if _sa(_normaliza_freguesia(n)) == alvo:
            return n
    ti = _tokens(freg)
    if not ti:
        return freg
    best, best_inter = None, set()
    for n, cj in cands:
        inter = ti & cj
        if len(inter) > len(best_inter):
            best, best_inter = n, inter
    # aceita se o Idealista é subconjunto do oficial, ou partilham um token
    # distintivo (>=4 letras) — evita casar por palavras curtas comuns.
    if best is not None and best_inter and (ti <= dict(cands)[best] or any(len(t) >= 4 for t in best_inter)):
        return best
    return freg


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
    """{slug: [(data, Path), ...]} dos consolidados <concelho>_<data>.json."""
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
    saida, vistos = [], set()
    for r in rows:
        if not _valido(r) or r["id"] in vistos:
            continue
        vistos.add(r["id"])
        r["concelho"] = CONCELHO_NOME[slug]
        r["distrito"] = CONCELHO_DIST[slug]
        r["freguesia"] = _reconcilia_freg(CONCELHO_NOME[slug], _normaliza_freguesia(r.get("freguesia")))
        saida.append(r)
    return saida


LISTING_FIELDS = [
    "id", "titulo", "link", "tipologia", "tamanho", "preco", "preco_por_metro",
    "preco_antigo", "desconto_pct", "freguesia", "concelho", "distrito", "agencia", "num_fotos",
]


def _stats(sub):
    return {
        "count": len(sub),
        "median_ppm": _median([s["preco_por_metro"] for s in sub]),
        "median_preco": _median([s["preco"] for s in sub]),
    }


def _history_por_nivel(por_concelho_files, listings):
    """Concelho e freguesia: série pelas datas próprias de cada concelho.
    Distrito e país: um único ponto do snapshot atual (data mais recente)."""
    conc, freg = defaultdict(list), defaultdict(list)
    for slug, ficheiros in por_concelho_files.items():
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
    data_max = max(d for fs in por_concelho_files.values() for d, _ in fs)
    por_dist = defaultdict(list)
    for it in listings:
        por_dist[it["distrito"]].append(it["preco_por_metro"])
    dist = {d: [{"date": data_max, "median_ppm": _median(ppms), "count": len(ppms)}] for d, ppms in por_dist.items()}
    pais = [{"date": data_max, "median_ppm": _median([it["preco_por_metro"] for it in listings]), "count": len(listings)}]
    return pais, dist, dict(conc), dict(freg)


def main():
    por_concelho_files = _ficheiros_por_concelho()
    if not por_concelho_files:
        print("Sem dados do Idealista em idealista/json/.")
        return
    data_max = max(fs[-1][0] for fs in por_concelho_files.values())

    listings = []
    for slug, ficheiros in por_concelho_files.items():
        _data, path = ficheiros[-1]
        for r in _carrega(slug, path):
            item = {k: r.get(k) for k in LISTING_FIELDS}
            item["categorias"] = detetar_categorias(r.get("descricao") or r.get("titulo") or "") or []
            item["grupo_agencia"] = _grupo_agencia(r.get("agencia"))
            listings.append(item)

    tamanho_grupo = defaultdict(int)
    for it in listings:
        tamanho_grupo[it["grupo_agencia"]] += 1
    for it in listings:
        g = it["grupo_agencia"]
        if g != "Independente" and tamanho_grupo[g] < MIN_AGENCIA_PROPRIA:
            it["grupo_agencia"] = OUTRAS

    # Agregados por nível
    por_dist = defaultdict(list)
    por_conc = defaultdict(list)
    por_freg = defaultdict(list)
    for it in listings:
        por_dist[it["distrito"]].append(it)
        por_conc[(it["distrito"], it["concelho"])].append(it)
        if it["freguesia"]:
            por_freg[(it["concelho"], it["freguesia"])].append(it)

    distritos = [{"distrito": d, **_stats(sub)} for d, sub in por_dist.items()]
    distritos.sort(key=lambda x: x["median_ppm"] or 0, reverse=True)

    concelhos = defaultdict(list)  # distrito -> [concelho stats]
    for (d, c), sub in por_conc.items():
        concelhos[d].append({"concelho": c, **_stats(sub)})
    for d in concelhos:
        concelhos[d].sort(key=lambda x: x["median_ppm"] or 0, reverse=True)

    freguesias = defaultdict(list)  # concelho -> [freguesia stats]
    for (c, fr), sub in por_freg.items():
        freguesias[c].append({"freguesia": fr, **_stats(sub)})
    for c in freguesias:
        freguesias[c].sort(key=lambda x: x["median_ppm"] or 0, reverse=True)

    hist_pais, hist_dist, hist_conc, hist_freg = _history_por_nivel(por_concelho_files, listings)

    cat_counts = defaultdict(int)
    for it in listings:
        for c in it["categorias"]:
            cat_counts[c] += 1
    com_desconto = [it for it in listings if it.get("desconto_pct") is not None]

    overview = {
        "total": len(listings),
        "distritos": len(por_dist),
        "concelhos": len(por_conc),
        "median_preco": _median([it["preco"] for it in listings]),
        "median_ppm": _median([it["preco_por_metro"] for it in listings]),
        "n_situacoes": sum(1 for it in listings if it["categorias"]),
        "n_com_desconto": len(com_desconto),
        "n_recolhas": len({d for fs in por_concelho_files.values() for d, _ in fs}),
        "date_max": data_max,
        "generated_at": datetime.now(ZoneInfo("Europe/Lisbon")).strftime("%Y-%m-%dT%H:%M"),
    }

    # slug de distrito e de concelho, para a página construir URLs de geo/nav.
    dist_slug = {v["distrito"]: v["distrito_slug"] for v in _INDEX.values()}
    conc_slug = {v["nome"]: slug for slug, v in _INDEX.items()}

    out = {
        "overview": overview,
        "categorias_label": CATEGORIAS_LABEL,
        "categorias_count": dict(cat_counts),
        "distritos": distritos,
        "concelhos": dict(concelhos),
        "freguesias": freguesias,
        "distrito_slug": dist_slug,
        "concelho_slug": {c: conc_slug[c] for c in conc_slug if c in {it["concelho"] for it in listings}},
        "history": {"pais": hist_pais, "distrito": hist_dist, "concelho": hist_conc, "freguesia": hist_freg},
        "listings": listings,
    }
    out_path = ROOT / "docs" / "idealista_data.json"
    out_path.write_text(json.dumps(out, ensure_ascii=False), encoding="utf-8")
    print(f"{len(listings)} anúncios (recolha {data_max}) -> {out_path}")
    print(f"  {len(por_dist)} distritos · {len(por_conc)} concelhos · "
          f"{sum(len(v) for v in freguesias.values())} freguesias · {overview['n_situacoes']} situações especiais")


if __name__ == "__main__":
    main()
