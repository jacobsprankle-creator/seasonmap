"""Appalachian GDD bloom stages (base 40F from Feb 1) + western superbloom potential from Oct-Mar precip percentile.

Phase 4 — not implemented in Phase 0.
"""
from __future__ import annotations

from typing import List

LAYER = "wildflower"
IMPLEMENTED = False


def dates_for(run_date: str) -> List[str]:
    raise NotImplementedError("Phase 4: wildflower layer")


def score(date: str):
    raise NotImplementedError("Phase 4: wildflower layer")
