from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]


def _load_stability_smoke():
    path = REPO_ROOT / "scripts/smoke/runner-stability-smoke.py"
    spec = importlib.util.spec_from_file_location("runner_stability_smoke", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


smoke = _load_stability_smoke()


def _pps_rows(values: list[int]) -> list[dict]:
    return [{"pps": value, "bps": value * 10} for value in values]


def test_pps_variance_high_cv_passes_by_default():
    rows = _pps_rows([100, 500, 120, 480, 90, 510])
    ok, detail = smoke._pps_variance_ok(rows, duration=60, strict=False)
    assert ok is True
    assert "exceeds" in detail
    assert "informational" in detail


def test_pps_variance_high_cv_fails_in_strict_mode():
    rows = _pps_rows([100, 500, 120, 480, 90, 510])
    ok, detail = smoke._pps_variance_ok(rows, duration=60, strict=True)
    assert ok is False
    assert detail.startswith("PPS variance ")
    assert "exceeds" in detail


def test_pps_variance_stable_passes_in_strict_mode():
    rows = _pps_rows([200, 210, 205, 198, 202, 201])
    ok, detail = smoke._pps_variance_ok(rows, duration=60, strict=True)
    assert ok is True
    assert "PPS variance" in detail
    assert "informational" not in detail


@pytest.mark.parametrize(
    ("rows", "expected_fragment"),
    [
        ([], "no JSON telemetry"),
        (_pps_rows([0, 0, 0]), "insufficient PPS samples"),
    ],
)
def test_pps_variance_missing_or_zero_telemetry_fails(rows, expected_fragment: str):
    ok, detail = smoke._pps_variance_ok(rows, duration=60, strict=False)
    assert ok is False
    assert expected_fragment in detail


def test_pps_variance_skipped_for_short_runs():
    ok, detail = smoke._pps_variance_ok(_pps_rows([1, 2, 3]), duration=15, strict=True)
    assert ok is True
    assert "skipped" in detail
