"""Shared test constants."""

import os

from pscx.common import MASTER_PSLX

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def master_available() -> bool:
    return os.path.exists(MASTER_PSLX)
