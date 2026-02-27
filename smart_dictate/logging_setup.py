from __future__ import annotations

import logging

from smart_dictate.paths import base_dir


def setup_logging(level: int = logging.INFO) -> None:
    log_dir = base_dir()
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / "smart-dictate.log"
    handlers = [
        logging.FileHandler(log_path, encoding="utf-8"),
        logging.StreamHandler(),
    ]
    logging.basicConfig(
        level=level,
        format="%(levelname)s %(name)s: %(message)s",
        handlers=handlers,
        force=True,
    )
