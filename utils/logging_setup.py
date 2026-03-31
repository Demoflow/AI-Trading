"""
Logging with daily rotation and audit trail.
"""

import os
import sys
from loguru import logger


def setup_logging(log_dir="logs"):
    os.makedirs(log_dir, exist_ok=True)
    logger.remove()
    logger.add(
        sys.stderr,
        level="INFO",
        format="{time:YYYY-MM-DD HH:mm:ss.SSS} | "
               "{level:7s} | {name}:{function}:{line} "
               "- {message}",
    )
    logger.add(
        os.path.join(log_dir, "trading_{time:YYYY-MM-DD}.log"),
        rotation="00:00",
        retention="90 days",
        level="DEBUG",
        format="{time:YYYY-MM-DD HH:mm:ss.SSS} | "
               "{level:7s} | {name}:{function}:{line} "
               "- {message}",
    )
    logger.add(
        os.path.join(log_dir, "errors_{time:YYYY-MM-DD}.log"),
        rotation="00:00",
        retention="90 days",
        level="ERROR",
        format="{time:YYYY-MM-DD HH:mm:ss.SSS} | "
               "{level:7s} | {name}:{function}:{line} "
               "- {message}",
    )
    logger.info("Logging initialized")
