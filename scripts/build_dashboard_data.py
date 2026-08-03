"""Agrega todos os JSON de scraping (sapo, imovirtual, century21, era, remax, supercasa)
num único dataset normalizado e calcula os agregados usados pelo dashboard."""
import json
import re
import statistics
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo
from collections import defaultdict

ROOT = Path(__file__).resolve().parent.parent
PORTAL_DIRS = ["sapo", "imovirtual", "century21", "era", "remax", "supercasa"]
PORTAL_ORIGEM = {
    "sapo": "Sapo", "imovirtual": "Imovirtual", "century21": "Century21",
    "era": "ERA", "remax": "Remax", "supercasa": "Supercasa",
}


def median(values):
    values = [v for v in values if v is not None]
    return round(statistics.median(values), 2) if values else None


def mean(values):
    values = [v for v in values if v is not None]
    return round(statistics.fmean(values), 2) if values else None


def percentile(values, pct):
    values = sorted(v for v in values if v is not None)
    if not values:
        return None
    if len(values) == 1:
        return round(values[0], 2)
    k = (len(values) - 1) * pct
    lo, hi = int(k), min(int(k) + 1, len(values) - 1)
    if lo == hi:
        return round(values[lo], 2)
    frac = k - lo
    return round(values[lo] + (values[hi] - values[lo]) * frac, 2)


MIN_PPM = 300
MAX_PPM = 20000
MIN_TAMANHO = 10

CONCELHO_NAMES = {
    "alenquer": "Alenquer",
    "amadora": "Amadora",
    "arruda-dos-vinhos": "Arruda dos Vinhos",
    "azambuja": "Azambuja",
    "cadaval": "Cadaval",
    "cascais": "Cascais",
    "lisboa": "Lisboa",
    "loures": "Loures",
    "lourinha": "Lourinhã",
    "mafra": "Mafra",
    "odivelas": "Odivelas",
    "oeiras": "Oeiras",
    "sintra": "Sintra",
    "sobral-de-monte-agraco": "Sobral de Monte Agraço",
    "torres-vedras": "Torres Vedras",
    "vila-franca-de-xira": "Vila Franca de Xira",
}


# bug conhecido no scraper do Supercasa (parcialmente corrigido na origem,
# mas ainda presente em dados já recolhidos): "freguesia" vem por vezes
# preenchida com o título do anúncio ou uma frase de marketing sem nenhuma
# estrutura de morada (ex. "Apartamento T1 em X", "VIVER LISBOA, SENTIR
# LISBOA", "estacionamento") em vez do nome real da freguesia.
_PALAVRAS_NAO_FREGUESIA = {
    "estacionamento", "arrecadacao", "varanda", "terraco", "elevador", "garagem",
    "piscina", "luxo", "novo", "nova", "remodelacao", "remodelado", "remodelada",
    "mobilado", "mobilada", "suite", "conforto", "premium", "espaco",
    "localizacao", "vista", "rio", "total", "unico", "sentir", "viver",
    "quinta", "herdade", "vivenda", "predio", "armazem", "terreno", "palacete", "solar",
}


def _sem_acentos(txt):
    subs = str.maketrans("áàâãäéèêëíìîïóòôõöúùûüç", "aaaaaeeeeiiiiooooouuuuc")
    return txt.lower().translate(subs)


# Em concelhos com freguesias fundidas em 2013 (Lisboa, Amadora, Odivelas, ...)
# tanto o Sapo ("localizacao") como o Supercasa (ld+json) devolvem o bairro
# informal seguido da freguesia oficial entre parênteses, ex. "Alameda (São
# Jorge de Arroios)" — sem isto o bairro (não oficial) ficava a valer por
# freguesia, fragmentando os dados em centenas de nomes não-oficiais.
def _normaliza_freguesia(fr):
    m = re.match(r"^.+\(([^()]+)\)\s*$", fr)
    fr = m.group(1).strip() if m else fr
    # "União das freguesias de Ramada e Caneças" / "União Freguesias do Cadaval
    # e Pêro Moniz" e a forma curta ("Ramada e Caneças") são a mesma freguesia
    # — normaliza-se para a forma curta, que é a mais comum entre as fontes.
    fr = re.sub(
        r"^Uni[aã]o(?:\s+das)?\s+freguesias\s+(?:de|do|da|dos|das)?\s*",
        "", fr, flags=re.IGNORECASE,
    ).strip()
    return fr


def _parece_freguesia(fr, concelho=None):
    if not fr or len(fr) < 3:
        return False
    if fr.isupper():
        return False
    if re.search(r"\d", fr):
        return False
    if re.match(r"^(apartamento\b|moradia\b|casa\b)", fr, re.IGNORECASE):
        return False
    # "Arruda dos Vinhos" sozinho pode ser a freguesia-sede do concelho (mesmo
    # nome é comum em Portugal) — só se rejeita quando é um sufixo de algo
    # maior, ex. "Herdade em Arruda dos Vinhos" (aí o texto antes não é freguesia).
    if concelho and fr.lower() != concelho.lower() and fr.lower().endswith(concelho.lower()):
        return False
    palavras = set(re.findall(r"[a-z]+", _sem_acentos(fr)))
    if palavras & _PALAVRAS_NAO_FREGUESIA:
        return False
    return True


def extract_freguesia(row, concelho):
    fr = row.get("freguesia")
    if fr:
        fr = _normaliza_freguesia(fr.strip())
        return fr if _parece_freguesia(fr, concelho) else None
    loc = row.get("localizacao")
    if loc:
        parts = [p.strip() for p in loc.split(",")]
        parts = [p for p in parts if p.lower() != concelho.lower() and "distrito" not in p.lower()]
        if parts:
            fr = _normaliza_freguesia(parts[0])
            return fr if _parece_freguesia(fr, concelho) else None
    return None


def valid_ppm(ppm, tamanho):
    # bug conhecido no scraper do Sapo: "tamanho" vem por vezes a 1 (placeholder),
    # o que faz preco_por_metro = preco inteiro — filtrar valores implausíveis
    if ppm is None:
        return None
    if tamanho is not None and tamanho < MIN_TAMANHO:
        return None
    if not (MIN_PPM <= ppm <= MAX_PPM):
        return None
    return ppm


def load_all():
    rows = []
    for portal in PORTAL_DIRS:
        json_dir = ROOT / portal / "json"
        if not json_dir.exists():
            continue
        for f in sorted(json_dir.glob("*.json")):
            m = re.match(r"([a-zA-Z-]+)_(\d{8})", f.stem)
            file_concelho = CONCELHO_NAMES.get(m.group(1)) if m else None
            file_date = m.group(2) if m else None
            try:
                data = json.loads(f.read_text(encoding="utf-8"))
            except Exception as e:
                print(f"skip {f}: {e}")
                continue
            for r in data:
                # o nome do ficheiro é a fonte fiável do concelho: o campo "concelho"
                # do scraper por vezes vem mal parseado a partir do título/morada
                concelho = (file_concelho or r.get("concelho") or "Desconhecido").strip()
                date = r.get("data_extracao") or file_date
                freguesia = extract_freguesia(r, concelho)
                preco = r.get("preco")
                tamanho = r.get("tamanho")
                ppm = r.get("preco_por_metro")
                if ppm is None and preco and tamanho:
                    try:
                        ppm = round(preco / tamanho, 2)
                    except ZeroDivisionError:
                        ppm = None
                ppm = valid_ppm(ppm, tamanho)
                rows.append({
                    "concelho": concelho,
                    "freguesia": freguesia,
                    "tipologia": r.get("tipologia"),
                    "preco": preco,
                    "tamanho": tamanho,
                    "preco_por_metro": ppm,
                    # tal como o concelho, o nome da pasta é a fonte fiável do portal:
                    # scrapes antigos do Sapo (antes de 20260714) nunca tiveram este campo
                    "origem": r.get("origem") or PORTAL_ORIGEM.get(portal),
                    "data_extracao": date,
                    "titulo": r.get("titulo"),
                    "link": r.get("link"),
                    "agencia": r.get("agencia") or r.get("anunciante"),
                    "num_fotos": r.get("num_fotos"),
                })

    # O Century21 obtém a freguesia através de uma API oficial de autocompletar
    # (ver century21.py: _freguesia_lookup) — por isso serve de lista de
    # referência para descartar nomes informais de bairro que outras fontes
    # (Sapo, Supercasa, ...) às vezes devolvem em vez da freguesia oficial
    # (ex. "Salitre", "Twin Towers" em Lisboa não são freguesias). Só se filtra
    # em concelhos onde o Century21 tem dados — nos restantes não há referência
    # fiável, por isso mantém-se o que já foi extraído.
    freguesias_oficiais = defaultdict(set)
    for r in rows:
        if r["origem"] == "Century21" and r["freguesia"]:
            freguesias_oficiais[r["concelho"]].add(r["freguesia"])
    # Concelhos rurais/pequenos onde o Century21 só tem anúncios numa única
    # freguesia não têm cobertura suficiente para servir de referência — aí
    # aplicar o filtro rejeitaria à força freguesias genuínas que só as
    # outras fontes captaram.
    for r in rows:
        oficiais = freguesias_oficiais.get(r["concelho"])
        if oficiais and len(oficiais) >= 2 and r["freguesia"] and r["freguesia"] not in oficiais:
            r["freguesia"] = None

    return rows


def latest_snapshot_rows(rows):
    """Para cada (portal, concelho), mantém só os anúncios da recolha mais recente
    desse par — evita misturar anúncios repetidos de recolhas antigas na análise
    de desvio face à média, que deve refletir o mercado atual."""
    latest_date = {}
    for r in rows:
        key = (r["origem"], r["concelho"])
        if r["data_extracao"] and (key not in latest_date or r["data_extracao"] > latest_date[key]):
            latest_date[key] = r["data_extracao"]
    return [r for r in rows if latest_date.get((r["origem"], r["concelho"])) == r["data_extracao"]]


MIN_GROUP_FOR_STATS = 8
MIN_GROUP_FOR_DEALS = 8
DEALS_PER_SIDE = 60
MIN_PRECO_ABSOLUTO = 20000
# Intervalo de desvio (vs. média do grupo concelho+tipologia) considerado um
# negócio plausível — abaixo de -30% é mais provável ser erro de recolha
# (preço/área mal capturados) do que uma pechincha genuína.
BEST_DEAL_MIN_PCT = -30.0
BEST_DEAL_MAX_PCT = -10.0
# Freguesias com só 1 anúncio no grupo dão uma "melhor oferta" pouco significativa
# (não há nada para comparar dentro da própria freguesia) — exige pelo menos 2.
MIN_LISTINGS_PER_FREGUESIA = 2
# Quantas candidatas por freguesia se guardam, para os filtros client-side
# (fotos/preço) ainda encontrarem uma oferta válida por freguesia.
N_CANDIDATOS_POR_FREGUESIA = 5

# intervalos de área plausíveis por tipologia (m²) — usados só para a análise de
# valor (média/desvio), que é sensível a outliers; a mediana usada no resto do
# dashboard já é robusta a estes casos
TAMANHO_RANGE = {
    "T0": (15, 120), "T1": (25, 160), "T2": (40, 220), "T3": (55, 300),
    "T4": (70, 400), "T5": (90, 500),
}
TAMANHO_RANGE_DEFAULT = (110, 800)  # T6 e acima


def plausible_for_value_analysis(r):
    # descarta timeshares/frações (preço muito abaixo do custo de uma fração real)
    if r["preco"] is not None and r["preco"] < MIN_PRECO_ABSOLUTO:
        return False
    titulo_lower = (r["titulo"] or "").lower()
    if re.search(r"time[\s-]?shar", titulo_lower):
        return False
    # descarta prédios/edifícios inteiros (preço total não comparável a uma fração)
    if re.search(r"\bpr[eé]dio\b", titulo_lower):
        return False
    # descarta áreas implausíveis para a tipologia (erro de scraping, ex. T0 de 720 m²)
    if r["tamanho"] is not None and r["tipologia"]:
        lo, hi = TAMANHO_RANGE.get(r["tipologia"], TAMANHO_RANGE_DEFAULT)
        if not (lo <= r["tamanho"] <= hi):
            return False
    return True


def build_value_analysis(rows):
    """Estatística de mercado por concelho + lista de anúncios concretos mais
    abaixo/acima da média local (mesmo concelho e tipologia), usando só o
    snapshot mais recente de cada portal para refletir o mercado atual."""
    latest = [r for r in latest_snapshot_rows(rows) if plausible_for_value_analysis(r)]
    concelhos = sorted({r["concelho"] for r in latest if r["concelho"]})

    concelho_stats = []
    for c in concelhos:
        ppms = [r["preco_por_metro"] for r in latest if r["concelho"] == c and r["preco_por_metro"] is not None]
        if len(ppms) < MIN_GROUP_FOR_STATS:
            continue
        m = mean(ppms)
        above = sum(1 for v in ppms if v > m)
        below = sum(1 for v in ppms if v < m)
        concelho_stats.append({
            "concelho": c,
            "count": len(ppms),
            "mean_ppm": m,
            "median_ppm": median(ppms),
            "p25_ppm": percentile(ppms, 0.25),
            "p75_ppm": percentile(ppms, 0.75),
            "min_ppm": round(min(ppms), 2),
            "max_ppm": round(max(ppms), 2),
            "pct_above_mean": round(100 * above / len(ppms), 1),
            "pct_below_mean": round(100 * below / len(ppms), 1),
        })
    concelho_stats.sort(key=lambda x: x["mean_ppm"], reverse=True)

    groups = defaultdict(list)
    for r in latest:
        if r["preco_por_metro"] is not None and r["tipologia"]:
            groups[(r["concelho"], r["tipologia"])].append(r)

    deals = []
    for (c, t), sub in groups.items():
        if len(sub) < MIN_GROUP_FOR_DEALS:
            continue
        m = mean([r["preco_por_metro"] for r in sub])
        for r in sub:
            deviation_pct = round(100 * (r["preco_por_metro"] - m) / m, 1)
            deals.append({
                "titulo": (r["titulo"] or "").strip(),
                "link": r["link"],
                "concelho": c,
                "freguesia": r["freguesia"],
                "tipologia": t,
                "tamanho": r["tamanho"],
                "preco": r["preco"],
                "preco_por_metro": r["preco_por_metro"],
                "num_fotos": r["num_fotos"],
                "group_mean_ppm": round(m, 2),
                "deviation_pct": deviation_pct,
                "origem": r["origem"],
                "agencia": (r["agencia"] or "").strip() or None,
                "data_extracao": r["data_extracao"],
            })
    deals.sort(key=lambda x: x["deviation_pct"])
    # Desvios muito extremos (< -30%) são mais prováveis de ser erro de
    # recolha (preço ou área mal capturados) do que negócios genuínos — os
    # "melhores negócios" ficam limitados a um intervalo plausível, em vez de
    # serem sempre dominados pelos desvios mais extremos da lista toda.
    deals_plausiveis = [d for d in deals if BEST_DEAL_MIN_PCT <= d["deviation_pct"] <= BEST_DEAL_MAX_PCT]
    best_deals = sorted(deals_plausiveis, key=lambda x: x["deviation_pct"])[:DEALS_PER_SIDE]
    overpriced = list(reversed(deals[-DEALS_PER_SIDE:]))

    # Uma oferta por freguesia (a melhor que a freguesia tem) — em vez de só
    # os negócios globais mais baratos, que tendem a ficar dominados por 1-2
    # freguesias muito baratas, dá para navegar por freguesia dentro do
    # concelho escolhido e ter mais opções de escolha espalhadas pelo mapa.
    por_freguesia = defaultdict(list)
    for d in deals_plausiveis:
        if d["freguesia"]:
            por_freguesia[(d["concelho"], d["freguesia"])].append(d)
    # Guardam-se as N melhores candidatas por freguesia (não só a 1ª) para que
    # os filtros de fotos/preço no dashboard (aplicados no browser) ainda
    # consigam encontrar uma oferta válida dessa freguesia, em vez de a
    # freguesia desaparecer só porque a sua oferta nº1 falhou o filtro.
    best_by_freguesia = [
        {**oferta, "num_anuncios_freguesia": len(ofertas)}
        for ofertas in por_freguesia.values()
        if len(ofertas) >= MIN_LISTINGS_PER_FREGUESIA
        for oferta in sorted(ofertas, key=lambda x: x["deviation_pct"])[:N_CANDIDATOS_POR_FREGUESIA]
    ]
    best_by_freguesia.sort(key=lambda x: x["deviation_pct"])

    return {
        "concelho_stats": concelho_stats,
        "best_deals": best_deals,
        "overpriced": overpriced,
        "best_by_freguesia": best_by_freguesia,
        "deals_meta": {
            "latest_snapshot_count": len(latest),
            "min_group_size": MIN_GROUP_FOR_DEALS,
        },
    }


def build_aggregates(rows):
    # "rows" tem um registo por anúncio POR recolha semanal — um anúncio que
    # continua no mercado há 3 meses aparece 12 vezes. Todas as agregações
    # (gráficos, contagens, freguesias) usam só a recolha mais recente de
    # cada (portal, concelho), para não contar o mesmo anúncio várias vezes
    # nem misturar anúncios antigos (já removidos do mercado) com os atuais.
    latest_all = latest_snapshot_rows(rows)

    concelhos = sorted({r["concelho"] for r in latest_all if r["concelho"]})
    origens = sorted({r["origem"] for r in rows if r["origem"]})
    dates = sorted({r["data_extracao"] for r in rows if r["data_extracao"]})

    date_max_by_origem = {}
    for o in origens:
        datas_origem = [r["data_extracao"] for r in rows if r["origem"] == o and r["data_extracao"]]
        date_max_by_origem[o] = max(datas_origem) if datas_origem else None

    overview = {
        "total_listings": len(latest_all),
        "concelhos": concelhos,
        "origens": origens,
        "date_min": dates[0] if dates else None,
        "date_max": dates[-1] if dates else None,
        "date_max_by_origem": date_max_by_origem,
        "n_dates": len(dates),
        "concelho_slugs": {nome: slug for slug, nome in CONCELHO_NAMES.items()},
    }

    by_concelho = []
    for c in concelhos:
        sub = [r for r in latest_all if r["concelho"] == c]
        by_concelho.append({
            "concelho": c,
            "count": len(sub),
            "median_ppm": median([r["preco_por_metro"] for r in sub]),
            "median_preco": median([r["preco"] for r in sub]),
        })
    by_concelho.sort(key=lambda x: (x["median_ppm"] or 0), reverse=True)

    tipologias_order = ["T0", "T1", "T2", "T3", "T4", "T5", "T6", "T7", "T8"]
    by_concelho_tipologia = []
    for c in concelhos:
        for t in tipologias_order:
            sub = [r for r in latest_all if r["concelho"] == c and r["tipologia"] == t]
            if not sub:
                continue
            by_concelho_tipologia.append({
                "concelho": c,
                "tipologia": t,
                "count": len(sub),
                "median_ppm": median([r["preco_por_metro"] for r in sub]),
                "median_preco": median([r["preco"] for r in sub]),
            })

    freg_map = defaultdict(list)
    for r in latest_all:
        if r["freguesia"]:
            freg_map[(r["concelho"], r["freguesia"])].append(r)
    by_freguesia = []
    for (c, f), sub in freg_map.items():
        if len(sub) < 3:
            continue
        ppms = [r["preco_por_metro"] for r in sub if r["preco_por_metro"] is not None]
        by_freguesia.append({
            "concelho": c,
            "freguesia": f,
            "count": len(sub),
            "median_ppm": median(ppms),
            "median_preco": median([r["preco"] for r in sub]),
            "mean_ppm": mean(ppms),
            "p25_ppm": percentile(ppms, 0.25),
            "p75_ppm": percentile(ppms, 0.75),
            "min_ppm": round(min(ppms), 2) if ppms else None,
            "max_ppm": round(max(ppms), 2) if ppms else None,
        })
    by_freguesia.sort(key=lambda x: (x["median_ppm"] or 0), reverse=True)

    by_origem = []
    for o in origens:
        sub = [r for r in latest_all if r["origem"] == o]
        by_origem.append({
            "origem": o,
            "count": len(sub),
            "median_ppm": median([r["preco_por_metro"] for r in sub]),
            "median_preco": median([r["preco"] for r in sub]),
        })
    by_origem.sort(key=lambda x: x["count"], reverse=True)

    by_concelho_origem = []
    for c in concelhos:
        for o in origens:
            sub = [r for r in latest_all if r["concelho"] == c and r["origem"] == o]
            if not sub:
                continue
            by_concelho_origem.append({
                "concelho": c,
                "origem": o,
                "count": len(sub),
                "median_ppm": median([r["preco_por_metro"] for r in sub]),
                "median_preco": median([r["preco"] for r in sub]),
            })

    trend_map = defaultdict(list)
    for r in rows:
        if r["data_extracao"] and r["concelho"]:
            trend_map[(r["data_extracao"], r["concelho"])].append(r)
    trend = []
    for (d, c), sub in trend_map.items():
        trend.append({
            "data": d,
            "concelho": c,
            "count": len(sub),
            "median_ppm": median([r["preco_por_metro"] for r in sub]),
        })
    trend.sort(key=lambda x: (x["concelho"], x["data"]))

    tip_counts = defaultdict(int)
    for r in latest_all:
        if r["tipologia"]:
            tip_counts[r["tipologia"]] += 1
    by_tipologia = [{"tipologia": t, "count": n} for t, n in sorted(tip_counts.items())]

    value_analysis = build_value_analysis(rows)

    return {
        "overview": overview,
        "by_concelho": by_concelho,
        "by_concelho_tipologia": by_concelho_tipologia,
        "by_freguesia": by_freguesia,
        "by_origem": by_origem,
        "by_concelho_origem": by_concelho_origem,
        "trend": trend,
        "by_tipologia": by_tipologia,
        "value_analysis": value_analysis,
    }


LISTING_FIELDS = [
    "titulo", "link", "freguesia", "tipologia", "tamanho", "preco",
    "preco_por_metro", "agencia", "origem", "num_fotos", "data_extracao",
]


def export_listings_by_concelho(rows):
    """Grava um ficheiro por concelho (docs/listings/<slug>.json) com todos os
    anúncios da recolha mais recente — carregado sob pedido pela secção de
    detalhe do concelho do dashboard, em vez de ir tudo num único ficheiro
    (seria grande demais: ~40 mil anúncios no total)."""
    slug_by_nome = {nome: slug for slug, nome in CONCELHO_NAMES.items()}
    latest = [r for r in latest_snapshot_rows(rows) if plausible_for_value_analysis(r)]

    por_concelho = defaultdict(list)
    for r in latest:
        por_concelho[r["concelho"]].append({k: r.get(k) for k in LISTING_FIELDS})

    listings_dir = ROOT / "docs" / "listings"
    listings_dir.mkdir(exist_ok=True)
    for f in listings_dir.glob("*.json"):
        f.unlink()

    resumo = []
    for nome, anuncios in por_concelho.items():
        slug = slug_by_nome.get(nome)
        if slug is None:
            continue
        anuncios.sort(key=lambda a: (a["preco_por_metro"] is None, a["preco_por_metro"]))
        (listings_dir / f"{slug}.json").write_text(
            json.dumps(anuncios, ensure_ascii=False), encoding="utf-8"
        )
        resumo.append((slug, len(anuncios)))
    return resumo


def main():
    rows = load_all()
    agg = build_aggregates(rows)
    agg["overview"]["generated_at"] = datetime.now(ZoneInfo("Europe/Lisbon")).strftime("%Y-%m-%dT%H:%M")
    out_path = ROOT / "docs" / "dashboard_data.json"
    out_path.write_text(json.dumps(agg, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"{len(rows)} registos processados -> {out_path}")
    print(json.dumps(agg["overview"], ensure_ascii=False, indent=2))

    resumo = export_listings_by_concelho(rows)
    total_listings = sum(n for _, n in resumo)
    print(f"{total_listings} anúncios exportados em {len(resumo)} ficheiros -> docs/listings/")


if __name__ == "__main__":
    main()
