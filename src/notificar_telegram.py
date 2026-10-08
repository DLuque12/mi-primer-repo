from __future__ import annotations

import html
import json
import os
from pathlib import Path

import requests

ALERT_SCORE = float(os.getenv("ALERT_SCORE", "75"))
MAX_RISK = int(os.getenv("MAX_ALERT_RISK", "15"))
MAX_ALERTS_PER_RUN = int(os.getenv("MAX_ALERTS_PER_RUN", "5"))


def load_json(path: Path, default):
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def save_ids(path: Path, ids: set[str]) -> None:
    path.write_text(
        json.dumps({"alerted_ids": sorted(ids)}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def detect_chat_ids(token: str) -> list[str]:
    """Devuelve todos los chats privados recientes que escribieron al bot."""
    if not token:
        return []
    try:
        response = requests.get(
            f"https://api.telegram.org/bot{token}/getUpdates",
            params={"limit": 100},
            timeout=20,
        )
        response.raise_for_status()
        updates = response.json().get("result", [])
        chat_ids: list[str] = []
        seen: set[str] = set()
        for update in updates:
            message = update.get("message") or update.get("edited_message") or {}
            chat = message.get("chat") or {}
            chat_id = chat.get("id")
            chat_type = chat.get("type")
            if chat_id is None or chat_type != "private":
                continue
            value = str(chat_id)
            if value not in seen:
                seen.add(value)
                chat_ids.append(value)
        return chat_ids
    except Exception as exc:
        print(f"Telegram: no se pudieron detectar destinatarios automáticamente: {exc}")
        return []


def configured_chat_ids() -> list[str]:
    """Permite además fijar destinatarios manualmente por secretos de GitHub."""
    values: list[str] = []
    single = os.getenv("TELEGRAM_CHAT_ID", "").strip()
    multiple = os.getenv("TELEGRAM_CHAT_IDS", "").strip()
    if single:
        values.append(single)
    if multiple:
        values.extend(x.strip() for x in multiple.split(",") if x.strip())
    return values


def unique(values: list[str]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        if value and value not in seen:
            seen.add(value)
            result.append(value)
    return result


def send_telegram(token: str, chat_id: str, text: str) -> bool:
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    response = requests.post(
        url,
        json={
            "chat_id": chat_id,
            "text": text,
            "parse_mode": "HTML",
            "disable_web_page_preview": True,
        },
        timeout=20,
    )
    if not response.ok:
        print(f"Telegram respondió {response.status_code} para un destinatario: {response.text[:200]}")
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


def format_listing(item: dict) -> str:
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

    details = [
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


def main() -> int:
    root = Path(__file__).resolve().parents[1]
    latest_path = root / "data" / "latest.json"
    state_path = root / "data" / "alerted_ids.json"

    payload = load_json(latest_path, {})
    listings = payload.get("listings", [])
    if not isinstance(listings, list):
        listings = []

    token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    chat_ids = unique(configured_chat_ids() + detect_chat_ids(token))

    if chat_ids:
        print(f"Telegram: {len(chat_ids)} destinatario(s) detectado(s).")
    else:
        print("Telegram: no hay destinatarios. Cada persona debe abrir el bot y enviar /start.")

    current_ids = {str(item.get("id")) for item in listings if item.get("id")}
    state = load_json(state_path, None)

    if state is None:
        save_ids(state_path, current_ids)
        print(f"Telegram: línea base creada con {len(current_ids)} avisos actuales.")
        return 0

    alerted = {str(x) for x in state.get("alerted_ids", [])}
    candidates = [
        item
        for item in listings
        if item.get("id")
        and str(item["id"]) not in alerted
        and float(item.get("opportunity_score") or 0) >= ALERT_SCORE
        and int(item.get("risk_score") or 0) <= MAX_RISK
    ]
    candidates.sort(key=lambda x: float(x.get("opportunity_score") or 0), reverse=True)
    candidates = candidates[:MAX_ALERTS_PER_RUN]

    if not candidates:
        print("Telegram: no hay oportunidades nuevas que superen el umbral.")
        return 0

    if not token or not chat_ids:
        print("Telegram: falta el token o no hay usuarios suscritos; no se enviaron alertas.")
        return 0

    sent_listings = 0
    total_messages = 0
    for item in candidates:
        delivered = 0
        text = format_listing(item)
        for chat_id in chat_ids:
            if send_telegram(token, chat_id, text):
                delivered += 1
                total_messages += 1
        if delivered:
            alerted.add(str(item["id"]))
            sent_listings += 1

    if sent_listings:
        save_ids(state_path, alerted)
    print(
        f"Telegram: {sent_listings} oportunidad(es) enviadas, "
        f"{total_messages} mensaje(s) en total a {len(chat_ids)} destinatario(s)."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
