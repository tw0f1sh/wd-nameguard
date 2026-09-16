from __future__ import annotations

import logging
from logging.handlers import RotatingFileHandler, TimedRotatingFileHandler
from pathlib import Path
from typing import Any


def setup_logging(config: dict[str, Any]) -> tuple[logging.Logger, logging.Logger]:
    log_dir = Path(str(config.get("directory", "./logs")))
    log_dir.mkdir(parents=True, exist_ok=True)
    level = getattr(logging, str(config.get("level", "INFO")).upper(), logging.INFO)
    rotation = config.get("rotation", {})

    app_logger = logging.getLogger("wardogs_nameguard")
    app_logger.setLevel(level)
    app_logger.handlers.clear()
    app_logger.propagate = False

    action_logger = logging.getLogger("wardogs_nameguard.actions")
    action_logger.setLevel(logging.INFO)
    action_logger.handlers.clear()
    action_logger.propagate = False

    app_handler = _make_handler(log_dir / "bot.log", rotation)
    app_handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    app_logger.addHandler(app_handler)

    console = logging.StreamHandler()
    console.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    console.setLevel(level)
    app_logger.addHandler(console)

    action_handler = _make_handler(log_dir / "actions.log", rotation)
    action_handler.setFormatter(logging.Formatter("%(message)s"))
    action_logger.addHandler(action_handler)

    return app_logger, action_logger


def _make_handler(path: Path, rotation: dict[str, Any]) -> logging.Handler:
    mode = str(rotation.get("mode", "size")).lower()
    backups = int(rotation.get("backups", 10))
    if mode == "time":
        return TimedRotatingFileHandler(
            path,
            when=str(rotation.get("when", "midnight")),
            interval=int(rotation.get("interval", 1)),
            backupCount=backups,
            encoding="utf-8",
            utc=bool(rotation.get("utc", False)),
        )
    return RotatingFileHandler(
        path,
        maxBytes=int(rotation.get("max_bytes", 5 * 1024 * 1024)),
        backupCount=backups,
        encoding="utf-8",
    )
