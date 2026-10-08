from __future__ import annotations

import json
import re
import statistics
from pathlib import Path

DISTRICTS = {
    "san-juan-de-miraflores": "San Juan de Miraflores",
    "santiago-de-surco": "Santiago de Surco",
    "magdalena-del-mar": "Magdalena del Mar",
    "villa-el-salvador": "Villa El Salvador",
    "lima-cercado": "Lima Cercado",
    "jesus-maria": "Jesús María",
    "pueblo-libre": "Pueblo Libre",
    "san-isidro": "San Isidro",
    "san-borja": "San Borja",
    "san-miguel": "San Miguel",
    "san-luis": "San Luis",
    "la-victoria": "La Victoria",
    "miraflores": "Miraflores",
    "surquillo": "Surquillo",
    "chorrillos": "Chorrillos",
    "barranco": "Barranco",
    "brena": "Breña",
    "lince": "Lince",
    "lurin": "Lurín",
}

OUTSIDE_CITY_TERMS = [
    "cañete", "canete", "barranca", "huaral", "huacho", "cerro azul",
    "san vicente de cañete", "san vicente de canete",
]


def detect_district(url: str, text: str) -> str | None:
    url_l = (url or "").lower()
    text_l = (text or "").lower()

    if any(term in url_l or term in text_l for term in OUTSIDE_CITY_TERMS):
        return None

    for slug, name in sorted(DISTRICTS.items(), key=lambda x: len(x[0]), reverse=True):
        variants = {slug, slug.replace("-", " "), name.lower()}
        if slug in url_l or any(v in text_l for v in variants):
            return name
    return None


def recompute_scores(items: list[dict]) -> None:
    groups: dict[tuple[str, str], list[float]] = {}
    for item in items:
        ppm2 = item.get("price_per_m2")
        if isinstance(ppm2, (int, float)) and ppm2 > 0:
            groups.setdefault((item["district"], item["property_type"]), []).append(float(ppm2))

    medians = {
        key: statistics.median(values)
        for key, values in groups.items()
        if len(values) >= 3
    }

    for item in items:
        price = int(item.get("price_usd") or 0)
        area = item.get("area_m2")
        risk = int(item.get("risk_score") or 0)
        score = 45.0

        if price <= 45_000:
            score += 8
        elif price <= 55_000:
            score += 4

        if isinstance(area, (int, float)):
            if item["property_type"] == "Departamento" and area >= 50:
                score += 4
            elif item["property_type"] == "Casa" and area >= 80:
                score += 4
            elif item["property_type"] == "Terreno" and area >= 90:
                score += 5

        text_l = (item.get("raw_text") or "").lower()
        if "ocasión" in text_l or "ocasion" in text_l:
            score += 6
        if "negociable" in text_l:
            score += 4
        if "urgente" in text_l:
            score += 5
        if "por debajo del mercado" in text_l:
            score += 8

        median = medians.get((item["district"], item["property_type"]))
        ppm2 = item.get("price_per_m2")
        if median and isinstance(ppm2, (int, float)) and ppm2 > 0:
            discount = (median - float(ppm2)) / median * 100
            item["discount_vs_median_pct"] = round(discount, 1)
            score += max(-15, min(35, discount * 0.8))
        else:
            item["discount_vs_median_pct"] = None

        score -= min(risk, 45)
        item["opportunity_score"] = round(max(0, min(100, score)), 1)


def render_markdown(payload: dict) -> str:
    items = sorted(
        payload["listings"],
        key=lambda x: (x.get("opportunity_score", 0), -x.get("risk_score", 0)),
        reverse=True,
    )[:30]

    lines = [
        "# Oportunidades inmobiliarias — Lima",
        "",
        f"Actualizado: **{payload.get('generated_at', '')}**",
        f"Tope: **US$ {payload.get('max_price_usd', 60000):,}**",
        "",
        "> Lista depurada para evitar avisos inyectados por los portales desde otros distritos.",
        "> El puntaje es orientativo: verifica partida registral, cargas, posesión e independización antes de separar.",
        "",
        "| Puntaje | Distrito | Tipo | Precio | Área | US$/m² | Riesgo | Aviso |",
        "|---:|---|---|---:|---:|---:|---:|---|",
    ]

    for item in items:
        area = f"{item['area_m2']:.1f}" if isinstance(item.get("area_m2"), (int, float)) else "—"
        ppm2 = f"{item['price_per_m2']:,.0f}" if isinstance(item.get("price_per_m2"), (int, float)) else "—"
        title = re.sub(r"\s+", " ", item.get("title") or f"{item['property_type']} en {item['district']}").strip()
        if "mantenimiento" in title.lower() or title.startswith("S/"):
            title = f"{item['property_type']} en {item['district']}"
        title = title.replace("|", "/")[:100]
        lines.append(
            f"| {item.get('opportunity_score', 0):.1f} | {item['district']} | {item['property_type']} | "
            f"${int(item['price_usd']):,} | {area} | {ppm2} | {item.get('risk_score', 0)} | "
            f"[{title}]({item['url']}) |"
        )

    lines.append("")
    return "\n".join(lines)


def main() -> None:
    root = Path(__file__).resolve().parents[1]
    json_path = root / "data" / "latest.json"
    md_path = root / "data" / "latest.md"

    payload = json.loads(json_path.read_text(encoding="utf-8"))
    cleaned = []
    seen = set()

    for item in payload.get("listings", []):
        district = detect_district(item.get("url", ""), item.get("raw_text", ""))
        if not district:
            continue
        item["district"] = district

        key = (
            item.get("url"),
            item.get("price_usd"),
            item.get("area_m2"),
        )
        if key in seen:
            continue
        seen.add(key)
        cleaned.append(item)

    recompute_scores(cleaned)
    cleaned.sort(key=lambda x: (x.get("opportunity_score", 0), -x.get("risk_score", 0)), reverse=True)
    payload["listings"] = cleaned
    payload["count"] = len(cleaned)
    payload["new_count"] = min(int(payload.get("new_count", 0)), len(cleaned))

    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    md_path.write_text(render_markdown(payload), encoding="utf-8")
    print(f"Postfiltro: {len(cleaned)} avisos válidos dentro de los distritos permitidos.")


if __name__ == "__main__":
    main()
