import asyncio
import logging
from pathlib import Path

from telethon import TelegramClient

from src.core import settings
from src.services.telegram.discovery import TelegramSourceDiscovery


async def main():
    Path(settings.TELEGRAM_SESSION_PATH).parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    client = TelegramClient(
        settings.TELEGRAM_SESSION_PATH,
        settings.TELEGRAM_API_ID,
        settings.TELEGRAM_API_HASH.get_secret_value(),
    )

    await client.start()

    try:
        discovery = TelegramSourceDiscovery(client)

        await discovery.discover(dialog_limit=200)

        print("\nПоиск и классификация источников завершены.")

    finally:
        await client.disconnect()


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    )

    asyncio.run(main())
