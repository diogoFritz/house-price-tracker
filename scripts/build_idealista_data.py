"""Gera docs/idealista_data.json — os dados da página de análise dedicada ao
Idealista (docs/idealista.html).

O Idealista é a única fonte com descrição completa + nº de fotos + descida de
preço fiáveis, por isso tem uma página própria com análises que o dashboard
principal não faz: deteção de "situações especiais" por palavras-chave na
descrição, descidas de preço (vendedor motivado), etc.

Lê o JSON consolidado mais recente de cada concelho (idealista/json/
<concelho>_<data>.json); para concelhos ainda em extração (sem consolidado),
concatena as páginas em cache (idealista/json/<concelho>/pagina_*.json).

Uso: python scripts/build_idealista_data.py
"""
import json
import sys
from datetime import datetime
from pathlib import Path
from collections import defaultdict
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from house_tracker.scraping.classificacao import categorias as detetar_categorias

ROOT = Path(__file__).resolve().parent.parent
IDEALISTA_DIR = ROOT / "idealista" / "json"

# €/m² fora deste intervalo é quase de certeza erro de recolha, não mercado.
MIN_PPM, MAX_PPM = 300, 20000
MIN_TAMANHO = 10

CATEGORIAS_LABEL = {
    "usufruto": "Usufruto / nua-propriedade",
    "arrendado": "Arrendado / investimento",
    "obras": "Precisa de obras",
    "heranca": "Herança / partilha",
    "permuta": "Aceita permuta",
    "urgente": "Vendedor motivado",
}

# slug -> nome do concelho (os ficheiros usam o slug no nome).
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


def _load_concelho(slug):
    """Anúncios de um concelho: prefere o consolidado mais recente; se não
    existir (concelho ainda em extração), concatena as páginas em cache."""
    consolidados = sorted(IDEALISTA_DIR.glob(f"{slug}_*.json"))
    if consolidados:
        return json.loads(consolidados[-1].read_text(encoding="utf-8"))
    pasta = IDEALISTA_DIR / slug
    if not pasta.exists():
        return []
    items = []
    for pagina in sorted(pasta.glob("pagina_*.json"), key=lambda p: int(p.stem.split("_")[1])):
        items.extend(json.loads(pagina.read_text(encoding="utf-8"))["items"])
    return items


def _valido(r):
    # Fantasmas (geo-reach-card do código antigo): sem link/preço.
    if not r.get("link") or r.get("preco") is None:
        return False
    ppm = r.get("preco_por_metro")
    if ppm is not None and not (MIN_PPM <= ppm <= MAX_PPM):
        return False
    if r.get("tamanho") is not None and r["tamanho"] < MIN_TAMANHO:
        return False
    return True


LISTING_FIELDS = [
    "id", "titulo", "link", "tipologia", "tamanho", "preco", "preco_por_metro",
    "preco_antigo", "desconto_pct", "freguesia", "concelho", "agencia", "num_fotos",
]


def main():
    listings = []
    vistos = set()
    for slug in CONCELHO_NOME:
        for r in _load_concelho(slug):
            if not _valido(r) or r["id"] in vistos:
                continue
            vistos.add(r["id"])
            item = {k: r.get(k) for k in LISTING_FIELDS}
            item["concelho"] = CONCELHO_NOME[slug]  # nome da pasta é a fonte fiável
            # Categorias a partir do texto — descrição não vai no JSON (poupa
            # espaço), só as etiquetas resultantes.
            cats = detetar_categorias(r.get("descricao") or r.get("titulo") or "")
            item["categorias"] = cats or []
            listings.append(item)

    por_concelho = defaultdict(list)
    for it in listings:
        por_concelho[it["concelho"]].append(it)
    by_concelho = []
    for nome, sub in por_concelho.items():
        by_concelho.append({
            "concelho": nome,
            "count": len(sub),
            "median_ppm": _median([s["preco_por_metro"] for s in sub]),
            "median_preco": _median([s["preco"] for s in sub]),
        })
    by_concelho.sort(key=lambda x: x["median_ppm"] or 0, reverse=True)

    cat_counts = defaultdict(int)
    for it in listings:
        for c in it["categorias"]:
            cat_counts[c] += 1

    com_desconto = [it for it in listings if it.get("desconto_pct") is not None]
    datas = [r.get("data_extracao") for slug in CONCELHO_NOME for r in _load_concelho(slug) if r.get("data_extracao")]

    overview = {
        "total": len(listings),
        "concelhos": len(por_concelho),
        "median_preco": _median([it["preco"] for it in listings]),
        "median_ppm": _median([it["preco_por_metro"] for it in listings]),
        "n_com_desconto": len(com_desconto),
        "n_situacoes": sum(1 for it in listings if it["categorias"]),
        "date_max": max(datas) if datas else None,
        "generated_at": datetime.now(ZoneInfo("Europe/Lisbon")).strftime("%Y-%m-%dT%H:%M"),
    }

    out = {
        "overview": overview,
        "by_concelho": by_concelho,
        "categorias_label": CATEGORIAS_LABEL,
        "categorias_count": dict(cat_counts),
        "listings": listings,
    }
    out_path = ROOT / "docs" / "idealista_data.json"
    out_path.write_text(json.dumps(out, ensure_ascii=False), encoding="utf-8")
    print(f"{len(listings)} anúncios -> {out_path}")
    print(f"  {len(by_concelho)} concelhos, {overview['n_situacoes']} com situação especial, "
          f"{len(com_desconto)} com descida de preço")
    print(f"  categorias: {dict(cat_counts)}")


if __name__ == "__main__":
    main()
