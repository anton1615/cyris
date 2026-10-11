"""The area birth threshold is not grade D: a deployment without it still runs and passes doctor."""

from pathlib import Path

import pytest
from fakes import SqliteD1, seed_d1_settings

from cyris.bootstrap import load_effective_config
from cyris.config import GRADE_D_KEYS
from cyris.diagnostics import doctor

pytestmark = pytest.mark.integration


def _d1_config(tmp_path: Path, d1: SqliteD1):
    seed_d1_settings(d1)
    return load_effective_config(tmp_path / "nope.toml", tmp_path / "nope.yaml")


def test_a_d1_with_every_required_key_and_no_threshold_passes_the_precheck(
    tmp_path: Path, d1: SqliteD1
) -> None:
    cfg = _d1_config(tmp_path, d1)

    assert cfg.missing_settings == []
    cfg.require_complete_settings()


def test_doctor_settings_check_passes_without_naming_areas_or_birth(
    tmp_path: Path, d1: SqliteD1
) -> None:
    cfg = _d1_config(tmp_path, d1)

    check = doctor._check_settings(cfg)

    assert check.status == "ok"
    assert "area" not in check.detail.lower()
    assert "birth" not in check.detail.lower()


def test_no_threshold_or_area_key_is_grade_d() -> None:
    assert not [k for k in GRADE_D_KEYS if k.startswith(("areas.", "area_"))]
