from __future__ import annotations

import hashlib
import json
import os
import re
import statistics
import sys
import time
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup

MAX_PRICE_USD = 60_000
REQUEST_TIMEOUT = 25
USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0 Safari/537.36"
)

DISTRICTS = {
    "lima-cercado": "Lima Cercado",
    "brena": "Breña",
    "la-victoria": "La Victoria",
    "miraflores": "Miraflores",
    "san-isidro": "San Isidro",
    "barranco": "Barranco",
    "san-borja": "San Borja",
    "surquillo": "Surquillo",
    "lince": "Lince",
    "magdalena-del-mar": "Magdalena del Mar",
    "jesus-maria": "Jesús María",
    "pueblo-libre": "Pueblo Libre",
    "san-miguel": "San Miguel",
    "santiago-de-surco": "Santiago de Surco",
    "san-luis": "San Luis",
    "chorrillos": "Chorrillos",
    "san-juan-de-miraflores": "San Juan de Miraflores",
    "villa-el-salvador": "Villa El Salvador",
    "lurin": "Lurín",
}
PROPERTY_TYPES = {
    "departamentos": "Departamento",
    "casas": "Casa",
    "terrenos": "Terreno",
}
EXCLUDED_TERMS = [
    "comas", "carabayllo", "los olivos", "puente piedra", "independencia",
    "san martin de porres", "san martín de porres", "villa maria del triunfo",
    "villa maría del triunfo", "pachacamac", "pachacámac",
]
RISK_TERMS = {
    "sin posesión": 35,
    "sin posesion": 35,
    "inmueble ocupado": 30,
    "ocupado": 20,
    "acciones y derechos": 30,
    "remate judicial": 20,
    "cesión de derechos": 25,
    "cesion de derechos": 25,
    "no independizado": 15,
}
BONUS_TERMS = {
    "ocasión": 6,
    "ocasion": 6,
    "negociable": 4,
    "urgente": 5,
    "por debajo del mercado": 8,
    "precio de ocasión": 8,
    "precio de ocasion": 8,
}


@dataclass
class Listing:
    id: str
    source: str
    property_type: str
    district: str
    title: str
    price_usd: int
    area_m2: float | None
    price_per_m2: float | None
    url: str
    raw_text: str
    risk_score: int = 0
    opportunity_score: float = 0.0
    discount_vs_median_pct: float | None = None


def district_from_text(text: str, fallback_slug: str) -> str:
    lower = text.lower()
    for slug, name in DISTRICTS.items():
        variants = {name.lower(), slug.replace("-", " ")}
        if any(v in lower for v in variants):
            return name
    return DISTRICTS[fallback_slug]


def source_urls() -> Iterable[tuple[str, str, str, str]]:
    for district_slug in DISTRICTS:
        for type_slug, type_name in PROPERTY_TYPES.items():
            yield (
                "Urbania",
                type_name,
                district_slug,
                f"https://urbania.pe/buscar/venta-de-{type_slug}-en-{district_slug}--lima--lima"
                "?currencyId=6&sort=low_price",
            )
            yield (
                "Adondevivir",
                type_name,
                district_slug,
                f"https://www.adondevivir.com/{type_slug}-en-venta-en-{district_slug}.html",
            )


def clean_text(value: str) -> str:
    return re.sub(r"\s+", " ", value or "").strip()


def extract_price_usd(text: str) -> int | None:
    patterns = [
        r"(?:USD|US\$|\$)\s*([0-9]{1,3}(?:[.,\s][0-9]{3})+|[0-9]{4,6})",
        r"([0-9]{1,3}(?:[.,\s][0-9]{3})+|[0-9]{4,6})\s*(?:USD|d[oó]lares)",
    ]
    candidates: list[int] = []
    for pattern in patterns:
        for match in re.findall(pattern, text, flags=re.I):
            digits = re.sub(r"\D", "", match)
            if not digits:
                continue
            value = int(digits)
            if 5_000 <= value <= 2_000_000:
                candidates.append(value)
    return min(candidates) if candidates else None


def extract_area_m2(text: str) -> float | None:
    patterns = [
        r"([0-9]{1,4}(?:[.,][0-9]{1,2})?)\s*m(?:²|2)\s*(?:tot(?:ales?)?\.?)?",
        r"[áa]rea(?:\s+total|\s+ocupada|\s+techada)?\s*[:\-]?\s*([0-9]{1,4}(?:[.,][0-9]{1,2})?)\s*m(?:²|2)",
    ]
    values = []
    for pattern in patterns:
        for match in re.findall(pattern, text, flags=re.I):
            try:
                value = float(match.replace(",", "."))
            except ValueError:
                continue
            if 12 <= value <= 5_000:
                values.append(value)
    return values[0] if values else None


def same_domain_or_relative(href: str, base_url: str) -> bool:
    if not href:
        return False
    if href.startswith("/"):
        return True
    return urlparse(href).netloc == urlparse(base_url).netloc


def candidate_blocks(soup: BeautifulSoup, base_url: str):
    seen = set()
    for anchor in soup.find_all("a", href=True):
        href = anchor.get("href", "")
        if not same_domain_or_relative(href, base_url):
            continue
        full_url = urljoin(base_url, href)
        if any(x in full_url for x in ("/buscar/", "/departamentos-en-", "/casas-en-", "/terrenos-en-")):
            continue

        block = anchor
        for _ in range(4):
            if block.parent is None:
                break
            block = block.parent
            text = clean_text(block.get_text(" ", strip=True))
            if 80 <= len(text) <= 3500 and extract_price_usd(text):
                break
        else:
            continue

        text = clean_text(block.get_text(" ", strip=True))
        if not text or not extract_price_usd(text):
            continue
        key = (full_url, text[:120])
        if key in seen:
            continue
        seen.add(key)
        yield anchor, block, full_url, text


def parse_page(source: str, property_type: str, district_slug: str, url: str, html: str) -> list[Listing]:
    soup = BeautifulSoup(html, "lxml")
    items: list[Listing] = []

    for anchor, block, full_url, text in candidate_blocks(soup, url):
        low = text.lower()
        if any(term in low for term in EXCLUDED_TERMS):
            continue

        price = extract_price_usd(text)
        if price is None or price > MAX_PRICE_USD:
            continue

        area = extract_area_m2(text)
        ppm2 = round(price / area, 2) if area else None
        if ppm2 is not None and not (100 <= ppm2 <= 15_000):
            ppm2 = None

        title_candidates = []
        for tag in block.find_all(["h1", "h2", "h3", "h4", "strong"]):
            candidate = clean_text(tag.get_text(" ", strip=True))
            if 8 <= len(candidate) <= 180 and "USD" not in candidate.upper():
                title_candidates.append(candidate)
        title = title_candidates[0] if title_candidates else clean_text(anchor.get_text(" ", strip=True))
        if not title:
            title = f"{property_type} en {DISTRICTS[district_slug]}"

        district = district_from_text(text, district_slug)
        risk_score = sum(penalty for term, penalty in RISK_TERMS.items() if term in low)
        listing_id = hashlib.sha1(full_url.encode("utf-8")).hexdigest()[:14]

        items.append(
            Listing(
                id=listing_id,
                source=source,
                property_type=property_type,
                district=district,
                title=title[:200],
                price_usd=price,
                area_m2=area,
                price_per_m2=ppm2,
                url=full_url,
                raw_text=text[:1200],
                risk_score=min(risk_score, 100),
            )
        )
    return items


def fetch(session: requests.Session, url: str) -> str | None:
    try:
        response = session.get(url, timeout=REQUEST_TIMEOUT, allow_redirects=True)
        if response.status_code != 200:
            print(f"[WARN] {response.status_code} {url}", file=sys.stderr)
            return None
        return response.text
    except requests.RequestException as exc:
        print(f"[WARN] {type(exc).__name__}: {url}", file=sys.stderr)
        return None


def dedupe(listings: list[Listing]) -> list[Listing]:
    result: dict[str, Listing] = {}
    for item in listings:
        normalized = re.sub(r"[^a-z0-9]", "", item.title.lower())
        key = f"{item.district}|{item.property_type}|{item.price_usd}|{item.area_m2}|{normalized[:60]}"
        current = result.get(key)
        if current is None or len(item.raw_text) > len(current.raw_text):
            result[key] = item
    return list(result.values())


def score_listings(listings: list[Listing]) -> None:
    groups: dict[tuple[str, str], list[float]] = {}
    for item in listings:
        if item.price_per_m2:
            groups.setdefault((item.district, item.property_type), []).append(item.price_per_m2)

    medians = {
        key: statistics.median(values)
        for key, values in groups.items()
        if len(values) >= 3
    }

    for item in listings:
        score = 45.0
        low = item.raw_text.lower()

        if item.price_usd <= 45_000:
            score += 8
        elif item.price_usd <= 55_000:
            score += 4

        if item.area_m2:
            if item.property_type == "Departamento" and item.area_m2 >= 50:
                score += 4
            elif item.property_type == "Casa" and item.area_m2 >= 80:
                score += 4
            elif item.property_type == "Terreno" and item.area_m2 >= 90:
                score += 5

        for term, bonus in BONUS_TERMS.items():
            if term in low:
                score += bonus

        median = medians.get((item.district, item.property_type))
        if median and item.price_per_m2:
            discount = (median - item.price_per_m2) / median * 100
            item.discount_vs_median_pct = round(discount, 1)
            score += max(-15, min(35, discount * 0.8))

        score -= min(item.risk_score, 45)
        item.opportunity_score = round(max(0, min(100, score)), 1)


def load_previous_ids(path: Path) -> set[str]:
    if not path.exists():
        return set()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return {str(item["id"]) for item in data.get("listings", [])}
    except Exception:
        return set()


def render_markdown(listings: list[Listing], new_ids: set[str], generated_at: str) -> str:
    top = sorted(listings, key=lambda x: (x.opportunity_score, -x.risk_score), reverse=True)[:30]
    lines = [
        "# Oportunidades inmobiliarias — Lima",
        "",
        f"Actualizado: **{generated_at}**",
        f"Tope: **US$ {MAX_PRICE_USD:,}**",
        "",
        "> El puntaje es orientativo. Una publicación barata puede esconder problemas legales, de posesión,",
        "> independización, cargas o errores del aviso. Verifica partida registral y documentación antes de separar.",
        "",
        "| Nueva | Puntaje | Distrito | Tipo | Precio | Área | US$/m² | Riesgo | Aviso |",
        "|---|---:|---|---|---:|---:|---:|---:|---|",
    ]
    for item in top:
        area = f"{item.area_m2:.1f}" if item.area_m2 else "—"
        ppm2 = f"{item.price_per_m2:,.0f}" if item.price_per_m2 else "—"
        new = "🆕" if item.id in new_ids else ""
        title = item.title.replace("|", "/")
        lines.append(
            f"| {new} | {item.opportunity_score:.1f} | {item.district} | {item.property_type} | "
            f"${item.price_usd:,} | {area} | {ppm2} | {item.risk_score} | [{title}]({item.url}) |"
        )
    lines.append("")
    return "\n".join(lines)


def main() -> int:
    root = Path(__file__).resolve().parents[1]
    data_dir = root / "data"
    data_dir.mkdir(exist_ok=True)
    latest_path = data_dir / "latest.json"
    old_ids = load_previous_ids(latest_path)

    session = requests.Session()
    session.headers.update({"User-Agent": USER_AGENT, "Accept-Language": "es-PE,es;q=0.9,en;q=0.5"})

    all_items: list[Listing] = []
    urls = list(source_urls())
    limit_env = os.getenv("MAX_SOURCE_PAGES")
    if limit_env:
        urls = urls[: int(limit_env)]

    for index, (source, property_type, district_slug, url) in enumerate(urls, start=1):
        print(f"[{index}/{len(urls)}] {source} | {property_type} | {DISTRICTS[district_slug]}")
        html = fetch(session, url)
        if html:
            all_items.extend(parse_page(source, property_type, district_slug, url, html))
        time.sleep(0.4)

    all_items = dedupe(all_items)
    score_listings(all_items)
    all_items.sort(key=lambda x: (x.opportunity_score, -x.risk_score), reverse=True)

    current_ids = {item.id for item in all_items}
    new_ids = current_ids - old_ids
    generated_at = datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")

    payload = {
        "generated_at": generated_at,
        "max_price_usd": MAX_PRICE_USD,
        "count": len(all_items),
        "new_count": len(new_ids),
        "listings": [asdict(item) for item in all_items],
    }
    latest_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    (data_dir / "latest.md").write_text(
        render_markdown(all_items, new_ids, generated_at),
        encoding="utf-8",
    )
    print(f"Guardadas {len(all_items)} propiedades ({len(new_ids)} nuevas).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
