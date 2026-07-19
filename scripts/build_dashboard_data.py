"""Agrega todos os JSON de scraping (sapo, imovirtual, century21, era, remax, supercasa)
num único dataset normalizado e calcula os agregados usados pelo dashboard."""
import json
import re
import statistics
from pathlib import Path
from collections import defaultdict

ROOT = Path(__file__).resolve().parent.parent
PORTAL_DIRS = ["sapo", "imovirtual", "century21", "era", "remax", "supercasa"]


def median(values):
    values = [v for v in values if v is not None]
    return round(statistics.median(values), 2) if values else None


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


def extract_freguesia(row, concelho):
    fr = row.get("freguesia")
    if fr:
        fr = fr.strip()
        # bug conhecido no scraper do Supercasa: por vezes "freguesia" vem
        # preenchida com o título do anúncio ("Apartamento T1 em X") em vez
        # do nome da freguesia — descartar esses casos
        if re.match(r"^Apartamento\b", fr, re.IGNORECASE):
            return None
        return fr
    loc = row.get("localizacao")
    if loc:
        parts = [p.strip() for p in loc.split(",")]
        parts = [p for p in parts if p.lower() != concelho.lower() and "distrito" not in p.lower()]
        if parts:
            return parts[0]
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
                    "origem": r.get("origem"),
                    "data_extracao": date,
                })
    return rows


def build_aggregates(rows):
    concelhos = sorted({r["concelho"] for r in rows if r["concelho"]})
    origens = sorted({r["origem"] for r in rows if r["origem"]})
    dates = sorted({r["data_extracao"] for r in rows if r["data_extracao"]})

    overview = {
        "total_listings": len(rows),
        "concelhos": concelhos,
        "origens": origens,
        "date_min": dates[0] if dates else None,
        "date_max": dates[-1] if dates else None,
        "n_dates": len(dates),
    }

    by_concelho = []
    for c in concelhos:
        sub = [r for r in rows if r["concelho"] == c]
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
            sub = [r for r in rows if r["concelho"] == c and r["tipologia"] == t]
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
    for r in rows:
        if r["freguesia"]:
            freg_map[(r["concelho"], r["freguesia"])].append(r)
    by_freguesia = []
    for (c, f), sub in freg_map.items():
        if len(sub) < 3:
            continue
        by_freguesia.append({
            "concelho": c,
            "freguesia": f,
            "count": len(sub),
            "median_ppm": median([r["preco_por_metro"] for r in sub]),
            "median_preco": median([r["preco"] for r in sub]),
        })
    by_freguesia.sort(key=lambda x: (x["median_ppm"] or 0), reverse=True)

    by_origem = []
    for o in origens:
        sub = [r for r in rows if r["origem"] == o]
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
            sub = [r for r in rows if r["concelho"] == c and r["origem"] == o]
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
    for r in rows:
        if r["tipologia"]:
            tip_counts[r["tipologia"]] += 1
    by_tipologia = [{"tipologia": t, "count": n} for t, n in sorted(tip_counts.items())]

    return {
        "overview": overview,
        "by_concelho": by_concelho,
        "by_concelho_tipologia": by_concelho_tipologia,
        "by_freguesia": by_freguesia,
        "by_origem": by_origem,
        "by_concelho_origem": by_concelho_origem,
        "trend": trend,
        "by_tipologia": by_tipologia,
    }


def main():
    rows = load_all()
    agg = build_aggregates(rows)
    out_path = ROOT / "scripts" / "dashboard_data.json"
    out_path.write_text(json.dumps(agg, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"{len(rows)} registos processados -> {out_path}")
    print(json.dumps(agg["overview"], ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
