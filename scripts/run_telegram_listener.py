import asyncio
import logging
from pathlib import Path

from src.core import settings
from src.integrations.telegram_listener import TelegramVacancyListener


async def main():
    Path(settings.TELEGRAM_SESSION_PATH).parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    listener = TelegramVacancyListener()
    await listener.start()


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    )

    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\nTelegram Listener остановлен.")
