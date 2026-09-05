"""The binary self-test must exercise real app behaviour and report failures."""

import json
import sys
from typing import TYPE_CHECKING

import pytest

import binary_smoke
import glidepath_binary
from glidepath import __version__
from glidepath.gui import main as gui_main

if TYPE_CHECKING:
    from pathlib import Path


def test_binary_smoke_round_trip(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Resources, rendering, persistence and spawned projections work together."""
    report = tmp_path / "report.json"
    monkeypatch.setattr(sys, "argv", ["glidepath.exe", "--smoke-test", str(report)])
    glidepath_binary.main()
    assert json.loads(report.read_text()) == {
        "version": __version__,
        "status": "passed",
    }


def test_binary_smoke_records_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A windowed executable still leaves a diagnostic and exits unsuccessfully."""

    def fail(_directory: Path) -> None:
        message = "A required resource is missing"
        raise RuntimeError(message)

    monkeypatch.setattr(binary_smoke, "exercise_app", fail)
    report = tmp_path / "report.json"
    with pytest.raises(RuntimeError, match="required resource"):
        binary_smoke.run_smoke_test(report)
    result = json.loads(report.read_text())
    assert result["status"] == "failed"
    assert "required resource" in result["error"]


def test_normal_binary_launch_uses_disclaimer_entry_point(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Normal launches retain the GUI's first-run disclaimer path."""
    launched: list[bool] = []
    monkeypatch.setattr(sys, "argv", ["glidepath.exe"])
    monkeypatch.setattr(gui_main, "main", lambda: launched.append(True))
    glidepath_binary.main()
    assert launched == [True]
