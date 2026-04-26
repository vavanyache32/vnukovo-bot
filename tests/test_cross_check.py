from __future__ import annotations

from src.core.cross_check import info_vs_resolve, neighbours, utc_vs_local
from src.models import Severity


def test_info_vs_resolve_within_tol() -> None:
    assert info_vs_resolve(9.4, 9.0) is None


def test_info_vs_resolve_disagreement() -> None:
    cr = info_vs_resolve(11.0, 9.0)
    assert cr is not None and cr.severity == Severity.WARNING


def test_utc_vs_local_critical() -> None:
    cr = utc_vs_local(18, 22)
    assert cr is not None and cr.severity == Severity.CRITICAL


def test_neighbours_anomaly() -> None:
    cr = neighbours(35.0, [10.0, 11.0])
    assert cr is not None and cr.severity == Severity.WARNING


def test_neighbours_normal() -> None:
    cr = neighbours(15.0, [14.0, 16.0])
    assert cr is None
