from __future__ import annotations

import base64
import hashlib
import html
import json
from pathlib import Path

import requests
from cryptography.fernet import Fernet, InvalidToken


def load_json(path: Path, default):
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def _fernet(token: str) -> Fernet:
    digest = hashlib.sha256(token.encode("utf-8")).digest()
    key = base64.urlsafe_b64encode(digest)
    return Fernet(key)


def load_subscribers(path: Path, token: str) -> set[str]:
    if not token or not path.exists():
        return set()
    try:
        encrypted = path.read_text(encoding="utf-8").strip().encode("ascii")
        if not encrypted:
            return set()
        data = json.loads(_fernet(token).decrypt(encrypted).decode("utf-8"))
        return {str(x) for x in data.get("chat_ids", []) if str(x).strip()}
    except (InvalidToken, ValueError, json.JSONDecodeError, UnicodeDecodeError):
        print("Telegram: no se pudo leer la lista cifrada de suscriptores.")
        return set()


def save_subscribers(path: Path, token: str, subscribers: set[str]) -> None:
    if not token:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(
        {"chat_ids": sorted(subscribers)}, ensure_ascii=False, separators=(",", ":")
    ).encode("utf-8")
    encrypted = _fernet(token).encrypt(payload).decode("ascii")
    path.write_text(encrypted + "\n", encoding="utf-8")


def load_sent_state(path: Path, token: str) -> dict[str, set[str]]:
    """Carga, cifrado, qué avisos ya recibió cada chat de Telegram."""
    if not token or not path.exists():
        return {}
    try:
        encrypted = path.read_text(encoding="utf-8").strip().encode("ascii")
        if not encrypted:
            return {}
        data = json.loads(_fernet(token).decrypt(encrypted).decode("utf-8"))
        raw = data.get("sent", {})
        if not isinstance(raw, dict):
            return {}
        return {
            str(chat_id): {str(item_id) for item_id in ids if str(item_id).strip()}
            for chat_id, ids in raw.items()
            if isinstance(ids, list)
        }
    except (InvalidToken, ValueError, json.JSONDecodeError, UnicodeDecodeError):
        print("Telegram: no se pudo leer el historial cifrado de avisos enviados.")
        return {}


def save_sent_state(path: Path, token: str, sent_state: dict[str, set[str]]) -> None:
    """Guarda cifrado el historial de avisos enviados por cada chat."""
    if not token:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(
        {
            "sent": {
                str(chat_id): sorted(str(item_id) for item_id in ids)
                for chat_id, ids in sent_state.items()
            }
        },
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")
    encrypted = _fernet(token).encrypt(payload).decode("ascii")
    path.write_text(encrypted + "\n", encoding="utf-8")


def send_telegram(token: str, chat_id: str, text: str) -> bool:
    response = requests.post(
        f"https://api.telegram.org/bot{token}/sendMessage",
        json={
            "chat_id": chat_id,
            "text": text,
            "parse_mode": "HTML",
            "disable_web_page_preview": True,
        },
        timeout=20,
    )
    if not response.ok:
        print(f"Telegram respondió {response.status_code}: {response.text[:300]}")
        return False
    return True


def parking_text(item: dict) -> str:
    status = item.get("parking_status")
    spaces = item.get("parking_spaces")
    if status == "included":
        if isinstance(spaces, int) and spaces > 0:
            return f"✅ incluida ({spaces})"
        return "✅ incluida"
    if status == "not_included":
        return "❌ no incluida"
    if status == "optional":
        return "➕ opcional / por separado"
    return "❔ no indicada en el aviso"


def format_listing(item: dict, already_sent: bool = False) -> str:
    title = html.escape((item.get("title") or "Oportunidad inmobiliaria")[:120])
    district = html.escape(str(item.get("district") or ""))
    property_type = html.escape(str(item.get("property_type") or ""))
    price = int(item.get("price_usd") or 0)
    area = item.get("area_m2")
    ppm2 = item.get("price_per_m2")
    score = float(item.get("opportunity_score") or 0)
    discount = item.get("discount_vs_median_pct")
    risk = int(item.get("risk_score") or 0)
    url = html.escape(str(item.get("url") or ""), quote=True)

    if already_sent:
        status_line = "🔁 <b>YA TE LA ENVIÉ ANTES</b>"
    else:
        status_line = "🆕 <b>NUEVO PARA TI</b>"

    details = [
        status_line,
        "🏠 <b>OPORTUNIDAD INMOBILIARIA</b>",
        f"📍 <b>{district}</b> · {property_type}",
        f"💵 <b>US$ {price:,}</b>",
    ]
    if isinstance(area, (int, float)):
        details.append(f"📐 {area:g} m²")
    if isinstance(ppm2, (int, float)):
        details.append(f"📊 US$ {ppm2:,.0f}/m²")
    if property_type != "Terreno":
        details.append(f"🚗 Cochera: {parking_text(item)}")
    if isinstance(discount, (int, float)) and discount > 0:
        details.append(f"📉 {discount:.1f}% por debajo de la mediana detectada")
    details.append(f"⭐ Puntaje: <b>{score:.1f}/100</b>")
    if risk:
        details.append(f"⚠️ Riesgo detectado: {risk}/100")
    details.extend(["", title, f'🔗 <a href="{url}">Ver publicación</a>'])
    return "\n".join(details)


def top_listings(payload: dict, limit: int = 5, min_score: float = 75, max_risk: int = 15) -> list[dict]:
    listings = payload.get("listings", [])
    if not isinstance(listings, list):
        return []
    items = [
        item
        for item in listings
        if float(item.get("opportunity_score") or 0) >= min_score
        and int(item.get("risk_score") or 0) <= max_risk
    ]
    items.sort(
        key=lambda x: (float(x.get("opportunity_score") or 0), -int(x.get("risk_score") or 0)),
        reverse=True,
    )
    return items[:limit]
