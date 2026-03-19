"""Simple level-aware logger; never log secrets. Correlation: job_id, issue_key."""
import logging
import os
import sys

LEVELS = {"DEBUG": logging.DEBUG, "INFO": logging.INFO, "WARN": logging.WARNING, "ERROR": logging.ERROR}


def get_logger(log_level: str):
    level = LEVELS.get(log_level.upper(), logging.INFO)
    logger = logging.getLogger("worker")
    logger.setLevel(level)
    if not logger.handlers:
        h = logging.StreamHandler(sys.stdout)
        h.setFormatter(logging.Formatter("[%(levelname)s] %(message)s"))
        logger.addHandler(h)
    return logger
