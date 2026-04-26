"""SYNOP (FM-12) parser — only the bits we care about for verification.

We mainly use the ``1sTxTxTx`` group (current temperature):

* ``s`` — sign (0 = positive, 1 = negative).
* ``TxTxTx`` — temperature × 10, in °C.

This is enough to cross-check METAR temperature; we don't need the full SYNOP.
"""
from __future__ import annotations

import re

_GROUP_TEMP = re.compile(r"\b1(\d)(\d{3})\b")


def parse_synop(text: str) -> float | None:
    """Return temperature in °C from a SYNOP body, or None if not found."""
    m = _GROUP_TEMP.search(text)
    if not m:
        return None
    sign, value = m.groups()
    val = int(value) / 10.0
    return -val if sign == "1" else val
