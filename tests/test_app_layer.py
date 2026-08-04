"""App-layer tests: copy, first-run state, shell view models (issue 8.1, §4.7)."""

import sys
from datetime import date
from importlib import metadata
from pathlib import Path

import pytest

from glidepath.app import (
    DISCLAIMER_BODY,
    FirstRunState,
    build_shell_view_model,
    default_state_path,
    load_state,
    record_disclaimer_acknowledged,
    record_last_plan_path,
    should_show_disclaimer,
)


class TestDisclaimerCopy:
    """The §1 product requirement is carried by the canonical copy."""

    def test_disclaimer_states_not_financial_advice(self) -> None:
        """The disclaimer must say the tool is not financial advice."""
        assert "not financial advice" in DISCLAIMER_BODY

    def test_disclaimer_states_not_regulated(self) -> None:
        """The disclaimer must say the tool is not regulated."""
        assert "not regulated" in DISCLAIMER_BODY


class TestFirstRunState:
    """Acknowledgement round-trips; anything unreadable degrades safely."""

    def test_missing_file_means_not_acknowledged(self, tmp_path: Path) -> None:
        """No state file yet — the disclaimer must show."""
        state = load_state(tmp_path / "settings.json")
        assert state.disclaimer_acknowledged_on is None

    def test_round_trip(self, tmp_path: Path) -> None:
        """Recording creates parent directories and reads back identically."""
        path = tmp_path / "nested" / "settings.json"
        acknowledged_on = date(2026, 8, 3)
        record_disclaimer_acknowledged(path, acknowledged_on)
        assert load_state(path).disclaimer_acknowledged_on == acknowledged_on

    @pytest.mark.parametrize(
        "content",
        [
            "not json at all",
            "[1, 2, 3]",
            "{}",
            '{"disclaimer_acknowledged_on": "not-a-date"}',
            '{"disclaimer_acknowledged_on": 42}',
            '{"disclaimer_acknowledged_on": "2026-08-03"}',
            '{"schema": 2, "disclaimer_acknowledged_on": "2026-08-03"}',
        ],
    )
    def test_defective_file_means_not_acknowledged(
        self, tmp_path: Path, content: str
    ) -> None:
        """A defective or unrecognised state file re-shows the disclaimer.

        That includes a valid-looking date under a missing or unknown
        schema version — only ``schema == 1`` acknowledgements count.
        """
        path = tmp_path / "settings.json"
        path.write_text(content, encoding="utf-8")
        assert load_state(path).disclaimer_acknowledged_on is None


class TestLastPlanPath:
    """The last plan path round-trips beside the acknowledgement."""

    def test_missing_file_means_no_last_plan(self, tmp_path: Path) -> None:
        """No state file yet — nothing to reopen."""
        assert load_state(tmp_path / "settings.json").last_plan_path is None

    def test_round_trip(self, tmp_path: Path) -> None:
        """Recording a plan path reads back identically."""
        path = tmp_path / "nested" / "settings.json"
        plan = tmp_path / "my-plan.glidepath.json"
        record_last_plan_path(path, plan)
        assert load_state(path).last_plan_path == plan

    def test_recording_the_plan_keeps_the_acknowledgement(self, tmp_path: Path) -> None:
        """Each record call preserves the other field's value."""
        path = tmp_path / "settings.json"
        acknowledged_on = date(2026, 8, 3)
        plan = tmp_path / "my-plan.glidepath.json"
        record_disclaimer_acknowledged(path, acknowledged_on)
        record_last_plan_path(path, plan)
        state = load_state(path)
        assert state.disclaimer_acknowledged_on == acknowledged_on
        assert state.last_plan_path == plan

    def test_recording_the_acknowledgement_keeps_the_plan(self, tmp_path: Path) -> None:
        """The disclaimer record never discards the remembered plan."""
        path = tmp_path / "settings.json"
        plan = tmp_path / "my-plan.glidepath.json"
        record_last_plan_path(path, plan)
        record_disclaimer_acknowledged(path, date(2026, 8, 3))
        assert load_state(path).last_plan_path == plan

    @pytest.mark.parametrize(
        "content",
        [
            '{"schema": 1, "last_plan_path": 42}',
            '{"schema": 1, "last_plan_path": ""}',
            '{"schema": 1}',
            '{"schema": 2, "last_plan_path": "plan.glidepath.json"}',
        ],
    )
    def test_defective_last_plan_degrades_to_none(
        self, tmp_path: Path, content: str
    ) -> None:
        """A defective or unrecognised plan path record reopens nothing."""
        path = tmp_path / "settings.json"
        path.write_text(content, encoding="utf-8")
        assert load_state(path).last_plan_path is None

    def test_defective_date_keeps_a_readable_plan_path(self, tmp_path: Path) -> None:
        """Each field degrades independently of the other."""
        path = tmp_path / "settings.json"
        path.write_text(
            '{"schema": 1, "disclaimer_acknowledged_on": "not-a-date",'
            ' "last_plan_path": "plan.glidepath.json"}',
            encoding="utf-8",
        )
        state = load_state(path)
        assert state.disclaimer_acknowledged_on is None
        assert state.last_plan_path == Path("plan.glidepath.json")


class TestDefaultStatePath:
    """Platform conventions: APPDATA on Windows, XDG config elsewhere."""

    def test_windows_uses_appdata(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """On Windows the state lives under %APPDATA%."""
        monkeypatch.setattr(sys, "platform", "win32")
        monkeypatch.setenv("APPDATA", str(Path("C:/Users/test/AppData/Roaming")))
        path = default_state_path()
        assert path.parts[-3:] == ("Roaming", "glidepath", "settings.json")

    def test_windows_falls_back_to_home(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Without %APPDATA% the conventional home-relative location is used."""
        monkeypatch.setattr(sys, "platform", "win32")
        monkeypatch.delenv("APPDATA", raising=False)
        path = default_state_path()
        assert path.parts[-3:] == ("Roaming", "glidepath", "settings.json")

    def test_unix_uses_xdg_config_home(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """On Unix the state honours $XDG_CONFIG_HOME."""
        monkeypatch.setattr(sys, "platform", "linux")
        monkeypatch.setenv("XDG_CONFIG_HOME", "/home/test/.config")
        path = default_state_path()
        assert path.parts[-3:] == (".config", "glidepath", "settings.json")

    def test_unix_falls_back_to_dot_config(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Without $XDG_CONFIG_HOME the state falls back to ~/.config."""
        monkeypatch.setattr(sys, "platform", "linux")
        monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
        path = default_state_path()
        assert path.parts[-3:] == (".config", "glidepath", "settings.json")


class TestShellViewModel:
    """The shell view model carries the canonical copy and version."""

    def test_disclaimer_view_model_carries_canonical_copy(self) -> None:
        """The disclaimer screen renders the canonical §1 wording."""
        view_model = build_shell_view_model()
        assert view_model.disclaimer.body == DISCLAIMER_BODY

    def test_about_repeats_disclaimer(self) -> None:
        """About must repeat the disclaimer (§1 product requirement)."""
        view_model = build_shell_view_model()
        assert DISCLAIMER_BODY in view_model.about.body

    def test_about_carries_installed_version(self) -> None:
        """About shows the installed distribution version."""
        view_model = build_shell_view_model()
        first_line = view_model.about.body.splitlines()[0]
        assert first_line.startswith("glidepath ")
        assert first_line != "glidepath unknown"

    def test_version_degrades_outside_an_install(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Version lookup failure degrades to a placeholder, not a crash."""

        def missing(_name: str) -> str:
            raise metadata.PackageNotFoundError

        monkeypatch.setattr(metadata, "version", missing)
        view_model = build_shell_view_model()
        assert view_model.about.body.startswith("glidepath unknown")

    def test_disclaimer_shows_until_acknowledged(self) -> None:
        """The disclaimer shows on every run until acknowledged, then never."""
        assert should_show_disclaimer(FirstRunState(disclaimer_acknowledged_on=None))
        assert not should_show_disclaimer(
            FirstRunState(disclaimer_acknowledged_on=date(2026, 8, 3))
        )
