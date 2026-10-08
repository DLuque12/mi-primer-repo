from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import requests

from telegram_utils import (
    format_listing,
    load_json,
    load_subscribers,
    save_subscribers,
    send_telegram,
    top_listings,
)


def send_current_offers(root: Path, token: str, chat_id: str, header: str) -> None:
    payload = load_json(root / "data" / "latest.json", {})
    items = top_listings(payload, limit=5)
    if not items:
        send_telegram(token, chat_id, "No encontré oportunidades que superen el filtro actual.")
        return
    send_telegram(token, chat_id, header)
    for item in items:
        send_telegram(token, chat_id, format_listing(item))


def fresh_search(root: Path) -> bool:
    try:
        subprocess.run([sys.executable, str(root / "src" / "buscador.py")], cwd=root, check=True)
        subprocess.run([sys.executable, str(root / "src" / "postfilter.py")], cwd=root, check=True)
        return True
    except subprocess.CalledProcessError as exc:
        print(f"Telegram: la búsqueda bajo demanda falló: {exc}")
        return False


def main() -> int:
    root = Path(__file__).resolve().parents[1]
    token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    if not token:
        print("Telegram: falta TELEGRAM_BOT_TOKEN.")
        return 0

    state_path = root / "data" / "telegram_update_state.json"
    subscribers_path = root / "data" / "telegram_subscribers.enc"
    state = load_json(state_path, {"last_update_id": 0})
    last_update_id = int(state.get("last_update_id") or 0)
    subscribers = load_subscribers(subscribers_path, token)

    response = requests.get(
        f"https://api.telegram.org/bot{token}/getUpdates",
        params={"offset": last_update_id + 1, "limit": 100, "timeout": 0},
        timeout=20,
    )
    response.raise_for_status()
    updates = response.json().get("result", [])
    if not updates:
        print("Telegram: no hay comandos nuevos.")
        return 0

    searched_now = False
    subscribers_changed = False
    max_update_id = last_update_id

    for update in updates:
        update_id = int(update.get("update_id") or 0)
        max_update_id = max(max_update_id, update_id)
        message = update.get("message") or update.get("edited_message") or {}
        chat = message.get("chat") or {}
        if chat.get("type") != "private":
            continue

        chat_id_raw = chat.get("id")
        text = str(message.get("text") or "").strip()
        if chat_id_raw is None or not text:
            continue
        chat_id = str(chat_id_raw)
        first = text.split()[0].lower()
        command = first.split("@", 1)[0]

        if command == "/stop" or command == "/salir":
            if chat_id in subscribers:
                subscribers.remove(chat_id)
                subscribers_changed = True
            send_telegram(token, chat_id, "🔕 Dejaste de recibir alertas. Puedes volver con /start.")
            continue

        if command in {"/start", "/ofertas", "/buscar", "/ayuda"} or text.lower() in {"hola", "ayuda"}:
            if chat_id not in subscribers:
                subscribers.add(chat_id)
                subscribers_changed = True

        if command == "/start":
            send_telegram(
                token,
                chat_id,
                "✅ <b>Bot activado</b>\n"
                "Recibirás nuevas oportunidades automáticamente.\n\n"
                "Comandos:\n"
                "/ofertas - muestra las mejores ofertas actuales\n"
                "/buscar - hace una búsqueda nueva ahora\n"
                "/ayuda - muestra los comandos\n"
                "/stop - deja de recibir alertas",
            )
            send_current_offers(root, token, chat_id, "📋 <b>Mejores oportunidades actuales</b>")
        elif command == "/ofertas":
            send_current_offers(root, token, chat_id, "📋 <b>Mejores oportunidades actuales</b>")
        elif command == "/buscar":
            send_telegram(
                token,
                chat_id,
                "🔎 Haré una búsqueda nueva en Urbania y Adondevivir. Puede tardar un par de minutos.",
            )
            if not searched_now:
                searched_now = fresh_search(root)
            if searched_now:
                send_current_offers(root, token, chat_id, "✅ <b>Búsqueda nueva terminada</b>")
            else:
                send_telegram(token, chat_id, "⚠️ No pude completar la búsqueda nueva. Prueba nuevamente más tarde.")
        elif command == "/ayuda" or text.lower() in {"hola", "ayuda"}:
            send_telegram(
                token,
                chat_id,
                "🤖 <b>Comandos disponibles</b>\n"
                "/ofertas - reenviar las 5 mejores ofertas guardadas\n"
                "/buscar - buscar propiedades nuevamente ahora\n"
                "/stop - dejar de recibir alertas",
            )
        else:
            send_telegram(token, chat_id, "No reconozco ese comando. Usa /ayuda.")

    state_path.parent.mkdir(parents=True, exist_ok=True)
    state_path.write_text(
        '{"last_update_id": ' + str(max_update_id) + '}\n',
        encoding="utf-8",
    )
    if subscribers_changed or not subscribers_path.exists():
        save_subscribers(subscribers_path, token, subscribers)

    print(
        f"Telegram: procesados {len(updates)} update(s); "
        f"suscriptores activos: {len(subscribers)}."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
