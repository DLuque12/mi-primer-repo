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


def detect_chat_id(token: str) -> str:
    if not token:
        return ""
    try:
        response = requests.get(
            f"https://api.telegram.org/bot{token}/getUpdates",
            timeout=20,
        )
        response.raise_for_status()
        updates = response.json().get("result", [])
        for update in reversed(updates):
            message = update.get("message") or update.get("edited_message") or {}
            chat = message.get("chat") or {}
            chat_id = chat.get("id")
            if chat_id is not None:
                return str(chat_id)
    except Exception as exc:
        print(f"Telegram: no se pudo detectar chat_id automáticamente: {exc}")
    return ""


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
        print(f"Telegram respondió {response.status_code}: {response.text[:300]}")
        return False
    return True


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
    chat_path = root / "data" / "telegram_chat.json"

    payload = load_json(latest_path, {})
    listings = payload.get("listings", [])
    if not isinstance(listings, list):
        listings = []

    token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    chat_id = os.getenv("TELEGRAM_CHAT_ID", "").strip()
    newly_detected = False
    if not chat_id:
        saved_chat = load_json(chat_path, {})
        chat_id = str(saved_chat.get("chat_id") or "").strip()
    if token and not chat_id:
        chat_id = detect_chat_id(token)
        if chat_id:
            chat_path.write_text(
                json.dumps({"chat_id": chat_id}, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            newly_detected = True
            print("Telegram: chat_id detectado automáticamente y guardado.")

    if token and chat_id and newly_detected:
        send_telegram(
            token,
            chat_id,
            "✅ <b>Conexión lista</b>\n"
            "Tu buscador inmobiliario quedó conectado a Telegram. "
            "Desde ahora recibirás aquí las oportunidades nuevas que superen el filtro.",
        )

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

    if not token or not chat_id:
        print("Telegram: falta el token o todavía no hay mensaje /start del usuario; no se enviaron alertas.")
        return 0

    sent = 0
    for item in candidates:
        if send_telegram(token, chat_id, format_listing(item)):
            alerted.add(str(item["id"]))
            sent += 1

    if sent:
        save_ids(state_path, alerted)
    print(f"Telegram: {sent} alerta(s) enviada(s).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
