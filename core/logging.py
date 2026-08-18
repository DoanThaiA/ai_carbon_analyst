import logging
import sys

def setup_logging(level: int = logging.INFO) -> None:
    """Configures the root logger for the application."""
    logging.basicConfig(
        level=level,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        handlers=[logging.StreamHandler(sys.stdout)],
    )
