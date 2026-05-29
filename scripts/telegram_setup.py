from __future__ import annotations

import argparse
import asyncio
from pathlib import Path

import httpx


DEFAULT_ENV = {
    "BOT_NAME": "terminal_check2_bot",
    "PORTS_CONFIG": "config/ports.yaml",
    "KEYWORDS_CONFIG": "config/keywords.yaml",
    "STATE_FILE": "data/terminal_check2_bot_state.json",
    "TIMEZONE": "Asia/Seoul",
    "SEND_EMPTY_REPORT": "false",
    "SEND_UNCHANGED_ALERTS": "false",
    "HTTP_TIMEOUT_SECONDS": "20",
    "HTTP_USER_AGENT": "terminal_check2_bot/1.0 (+official-port-notice-monitor)",
    "LOG_LEVEL": "INFO",
}


async def main() -> None:
    parser = argparse.ArgumentParser(description="Configure Telegram credentials for terminal_check2_bot.")
    parser.add_argument("--token", help="Telegram bot token from @BotFather")
    parser.add_argument("--chat-id", help="Telegram chat id to receive alerts")
    parser.add_argument("--discover-chat", action="store_true", help="Read getUpdates and use the latest chat id")
    parser.add_argument("--test", action="store_true", help="Send a test message after writing .env")
    parser.add_argument("--env-file", default=".env", help="Path to .env file")
    args = parser.parse_args()

    token = args.token or input("TELEGRAM_BOT_TOKEN: ").strip()
    if not token:
        raise SystemExit("TELEGRAM_BOT_TOKEN is required.")

    bot_info = await telegram_get(token, "getMe")
    username = bot_info["result"].get("username", "(unknown)")
    print(f"Telegram bot verified: @{username}")

    chat_id = args.chat_id
    if args.discover_chat and not chat_id:
        print("Send any message to the bot in Telegram, then press Enter here.")
        input()
        chat_id = await discover_latest_chat_id(token)
        print(f"Discovered TELEGRAM_CHAT_ID: {chat_id}")

    if not chat_id:
        chat_id = input("TELEGRAM_CHAT_ID: ").strip()
    if not chat_id:
        raise SystemExit("TELEGRAM_CHAT_ID is required.")

    env_path = Path(args.env_file)
    env_values = {**DEFAULT_ENV, "TELEGRAM_BOT_TOKEN": token, "TELEGRAM_CHAT_ID": chat_id}
    write_env(env_path, env_values)
    print(f"Wrote {env_path}")

    if args.test:
        await telegram_post(
            token,
            "sendMessage",
            {
                "chat_id": chat_id,
                "text": "terminal_check2_bot 테스트 메시지입니다. Telegram 연결이 정상입니다.",
            },
        )
        print("Test message sent.")


async def discover_latest_chat_id(token: str) -> str:
    payload = await telegram_get(token, "getUpdates")
    updates = payload.get("result", [])
    if not updates:
        raise SystemExit("No updates found. Send a message to the bot first, then retry.")

    for update in reversed(updates):
        message = update.get("message") or update.get("channel_post") or update.get("edited_message")
        if message and "chat" in message:
            return str(message["chat"]["id"])

    raise SystemExit("No chat id found in updates.")


async def telegram_get(token: str, method: str) -> dict:
    url = f"https://api.telegram.org/bot{token}/{method}"
    async with httpx.AsyncClient(timeout=20) as client:
        response = await client.get(url)
    return parse_telegram_response(response)


async def telegram_post(token: str, method: str, payload: dict) -> dict:
    url = f"https://api.telegram.org/bot{token}/{method}"
    async with httpx.AsyncClient(timeout=20) as client:
        response = await client.post(url, json=payload)
    return parse_telegram_response(response)


def parse_telegram_response(response: httpx.Response) -> dict:
    try:
        payload = response.json()
    except ValueError as exc:
        raise SystemExit(f"Telegram returned non-JSON response: HTTP {response.status_code}") from exc

    if response.status_code >= 400 or not payload.get("ok"):
        description = payload.get("description", response.text)
        raise SystemExit(f"Telegram API error: HTTP {response.status_code} {description}")
    return payload


def write_env(path: Path, values: dict[str, str]) -> None:
    lines = [f"{key}={value}" for key, value in values.items()]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


if __name__ == "__main__":
    asyncio.run(main())
