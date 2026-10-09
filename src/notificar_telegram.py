from __future__ import annotations

import json
import os
from pathlib import Path

from telegram_utils import (
    format_listing,
    load_json,
    load_sent_state,
    load_subscribers,
    save_sent_state,
    send_telegram,
)

ALERT_SCORE = float(os.getenv("ALERT_SCORE", "75"))
MAX_RISK = int(os.getenv("MAX_ALERT_RISK", "15"))
MAX_ALERTS_PER_RUN = int(os.getenv("MAX_ALERTS_PER_RUN", "5"))


def save_ids(path: Path, ids: set[str]) -> None:
    path.write_text(
        json.dumps({"alerted_ids": sorted(ids)}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def main() -> int:
    root = Path(__file__).resolve().parents[1]
    latest_path = root / "data" / "latest.json"
    state_path = root / "data" / "alerted_ids.json"
    subscribers_path = root / "data" / "telegram_subscribers.enc"
    sent_path = root / "data" / "telegram_sent.enc"

    payload = load_json(latest_path, {})
    listings = payload.get("listings", [])
    if not isinstance(listings, list):
        listings = []

    token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    if not token:
        print("Telegram: falta TELEGRAM_BOT_TOKEN.")
        return 0

    subscribers = load_subscribers(subscribers_path, token)
    if not subscribers:
        print("Telegram: todavía no hay suscriptores registrados.")
        return 0

    sent_state = load_sent_state(sent_path, token)
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

    sent_items = 0
    sent_messages = 0
    history_changed = False

    for item in candidates:
        delivered = False
        item_id = str(item["id"])

        for chat_id in sorted(subscribers):
            seen_by_chat = sent_state.setdefault(chat_id, set())
            already_sent = item_id in seen_by_chat
            message = format_listing(item, already_sent=already_sent)
            if send_telegram(token, chat_id, message):
                delivered = True
                sent_messages += 1
                if item_id not in seen_by_chat:
                    seen_by_chat.add(item_id)
                    history_changed = True

        if delivered:
            alerted.add(item_id)
            sent_items += 1

    if sent_items:
        save_ids(state_path, alerted)
    if history_changed:
        save_sent_state(sent_path, token, sent_state)

    print(
        f"Telegram: {sent_items} oportunidad(es) enviada(s) en "
        f"{sent_messages} mensaje(s) a {len(subscribers)} suscriptor(es)."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
