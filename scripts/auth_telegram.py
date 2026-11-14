import asyncio
import logging
from pathlib import Path

from telethon import TelegramClient
from telethon.errors import SessionPasswordNeededError

from src.core import settings

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


async def main():
    session_path = Path(settings.TELEGRAM_SESSION_PATH)

    # Создаём каталог для файла сессии.
    session_path.parent.mkdir(parents=True, exist_ok=True)

    client = TelegramClient(
        str(session_path),
        settings.TELEGRAM_API_ID,
        settings.TELEGRAM_API_HASH.get_secret_value(),
    )

    await client.connect()

    try:
        if await client.is_user_authorized():
            me = await client.get_me()

            print(f"\n✅ Аккаунт уже авторизован: {me.username or me.id}")
            return

        print("\n=== TELEGRAM AUTHORIZATION ===")

        phone = input("Введите номер телефона в международном формате: ").strip()

        if not phone:
            print("❌ Номер телефона не указан.")
            return

        await client.send_code_request(phone)

        code = input("Введите код подтверждения из Telegram: ").strip()

        if not code:
            print("❌ Код не указан.")
            return

        try:
            await client.sign_in(
                phone=phone,
                code=code,
            )

        except SessionPasswordNeededError:
            password = input("Включена двухфакторная защита. Введите пароль Telegram: ")

            await client.sign_in(password=password)

        me = await client.get_me()

        print("\n✅ Авторизация успешно выполнена!")
        print(f"Аккаунт: {me.username or me.id}")
        print(f"Сессия сохранена: {session_path}")

    finally:
        await client.disconnect()


if __name__ == "__main__":
    asyncio.run(main())
