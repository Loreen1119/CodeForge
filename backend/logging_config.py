"""
logging_config.py — 全后端统一日志配置

使用方式（在每个模块顶部）:
    import logging
    logger = logging.getLogger(__name__)

日志级别（可通过 LOG_LEVEL 环境变量覆盖）:
    INFO  — 默认
    DEBUG — 开发调试
    WARNING — 仅告警

模块日志级别覆盖（通过 LOG_OVERRIDE 环境变量，格式 "mod1=DEBUG,mod2=WARNING"）:
    例: LOG_OVERRIDE="task_service=DEBUG,repository=WARNING"
"""

from __future__ import annotations

import logging
import os
import sys


def setup_logging() -> None:
    level_name = os.getenv("LOG_LEVEL", "INFO").upper()
    level = getattr(logging, level_name, logging.INFO)

    fmt = "%(asctime)s [%(levelname).1s] [%(name)s] %(message)s"
    datefmt = "%H:%M:%S"

    # Root logger
    logging.basicConfig(level=level, format=fmt, datefmt=datefmt, stream=sys.stdout)

    # Per-module overrides
    overrides = os.getenv("LOG_OVERRIDE", "")
    if overrides:
        for pair in overrides.split(","):
            pair = pair.strip()
            if "=" in pair:
                mod, lvl = pair.split("=", 1)
                mod = mod.strip()
                lvl = lvl.strip().upper()
                lvl_num = getattr(logging, lvl, None)
                if lvl_num is not None:
                    logging.getLogger(mod).setLevel(lvl_num)

    # Suppress noisy third-party loggers
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("openai").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
