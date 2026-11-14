import logging

from src.agents.source_manager import TelegramSourceManager

if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    )

    manager = TelegramSourceManager()
    manager.run()
