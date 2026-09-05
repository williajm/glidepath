"""Exercise the actual compiled application, including spawned Monte Carlo workers.

Only invoked by the binary's explicit --smoke-test REPORT.json option.
All plan/settings writes stay inside a temporary directory. Normal launches
still go through the first-run disclaimer in glidepath.gui.main.
"""

import json
import multiprocessing
from concurrent.futures import ProcessPoolExecutor
from datetime import UTC, datetime
from pathlib import Path
from tempfile import TemporaryDirectory

from PySide6.QtWidgets import QApplication

from glidepath import __version__
from glidepath.app import (
    build_shell_view_model,
    initial_plan_state,
    load_plan_state,
    parse_facts_form,
    save_plan_state,
    state_with_household,
)
from glidepath.app.example import example_facts_form_data
from glidepath.app.plan import plan_run_config, region_for
from glidepath.core import PathParallelism, RunMode, run_paths
from glidepath.gui.style import app_icon, apply_theme, wordmark_pixmap
from glidepath.gui.widgets import MainWindow
from glidepath.regions.uk import load_returns_history


def check(condition: bool, message: str) -> None:  # noqa: FBT001 — assertion helper, not a behaviour switch.
    """Keep checks active even if the binary is built with assertions disabled."""
    if not condition:
        raise RuntimeError(message)


def exercise_app(directory: Path) -> None:
    """Load resources, project and round-trip a plan, and compare worker results."""
    app = QApplication.instance() or QApplication([])
    check(isinstance(app, QApplication), "A QApplication is required")
    if isinstance(app, QApplication):
        apply_theme(app)
    check(not app_icon().isNull(), "Missing application icon")
    check(not wordmark_pixmap().isNull(), "Missing wordmark")
    load_returns_history()

    now = datetime(2026, 9, 5, tzinfo=UTC)
    today = now.date()
    facts = parse_facts_form(example_facts_form_data(), recorded_on=now, today=today)
    if facts.household is None:
        message = "Could not parse the example plan"
        raise RuntimeError(message)
    state = state_with_household(initial_plan_state(), facts.household, today=today)
    check(state.result is not None, f"Projection failed: {state.run_error}")
    plan_path = directory / "example.glidepath.json"
    saved = save_plan_state(state, plan_path)
    check(saved.saved, saved.message)
    loaded = load_plan_state(plan_path, today=today)
    if loaded.state is None:
        raise RuntimeError(loaded.message)
    check(loaded.state.household == state.household, "Saved plan changed on reload")
    check(loaded.state.result == state.result, "Projection changed on reload")

    config = plan_run_config(
        facts.household, today=today, mode=RunMode.MONTE_CARLO, seed=7
    )
    region = region_for(state.assumptions)
    serial = run_paths(facts.household, state.assumptions, region, config, paths=2)
    with ProcessPoolExecutor(
        max_workers=2, mp_context=multiprocessing.get_context("spawn")
    ) as executor:
        parallel = run_paths(
            facts.household,
            state.assumptions,
            region,
            config,
            paths=2,
            parallelism=PathParallelism(executor=executor, workers=2),
        )
    check(parallel == serial, "Spawned Monte Carlo results differ from serial results")

    window = MainWindow(
        build_shell_view_model(), settings_path=directory / "state.json"
    )
    check(window.open_plan(plan_path), "The GUI could not open the saved plan")
    window.show()
    app.processEvents()
    check(not window.grab().isNull(), "The main window did not render")
    window.close()
    window.deleteLater()
    app.processEvents()


def run_smoke_test(report_path: Path) -> None:
    """Write a success/failure report even when the Windows console is disabled."""
    report: dict[str, str] = {"version": __version__, "status": "failed"}
    try:
        with TemporaryDirectory(prefix="glidepath-smoke-") as temporary:
            exercise_app(Path(temporary))
        report["status"] = "passed"
    except Exception as exc:
        report["error"] = f"{type(exc).__name__}: {exc}"
        raise
    finally:
        report_path.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
