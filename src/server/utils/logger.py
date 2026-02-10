# src/server/utils/logger.py
"""Structured logging configuration using structlog.
All modules should import logger via:
    from src.server.utils.logger import logger
"""
import logging
import os
import sys
from logging.handlers import RotatingFileHandler

import structlog


def configure_logging(level: str = "INFO"):
    handlers = [logging.StreamHandler(sys.stderr)]

    log_file = os.getenv("LOG_FILE")
    if log_file:
        log_dir = os.path.dirname(log_file)
        if log_dir:
            os.makedirs(log_dir, exist_ok=True)
            print(f"[log] created log directory: {log_dir}", file=sys.stderr)
        print(f"[log] writing to file: {log_file}", file=sys.stderr)
        handlers.append(
            RotatingFileHandler(
                log_file,
                maxBytes=50 * 1024 * 1024,
                backupCount=5,
                encoding="utf-8",
            )
        )

    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
        handlers=handlers,
    )
    structlog.configure(
        processors=[
            structlog.processors.TimeStamper(fmt="%Y-%m-%d %H:%M:%S"),
            # 禁用 ASCII 转义以支持 emoji 表情符号的正常显示
            structlog.processors.JSONRenderer(ensure_ascii=False),
        ],
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=True,
    )


# Initialize at import time
configure_logging()
logger = structlog.get_logger()
