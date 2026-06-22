import logging
import sys


def configure_application_logging() -> None:
    """Configure the application's console logger without changing Uvicorn logs."""
    logger = logging.getLogger("tiktok_api")
    if logger.handlers:
        return

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(
        logging.Formatter("%(asctime)s - %(name)s - %(levelname)s - %(message)s")
    )
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)
    logger.propagate = False
