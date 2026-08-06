"""Golden byte snapshot of the cash-flow CSV export (issue #133; 9.19).

The export tests in ``test_app_exports.py`` pin every cell against the
report model, but a column reorder, a locale-sensitive number format,
or a changed line terminator would still ship silently. This snapshot
pins the exact bytes the launch example exports — header block, column
order, quoting, and CRLF row terminators included.

To accept an intentional change, regenerate with

    uv run --locked pytest --no-cov tests/test_export_golden.py --update-golden

(``--no-cov``: the repository-wide coverage gate would otherwise fail a
single-module run) then review the diff and explain it in the pull
request. The golden is exempt from git's EOL normalisation (see
``.gitattributes``) so its CRLF row terminators survive checkout on
every platform.
"""

from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING

from glidepath.app import (
    cash_flow_csv,
    example_facts_form_data,
    initial_plan_state,
    parse_facts_form,
    state_with_household,
)
from glidepath.core import ReportBasis

if TYPE_CHECKING:
    import pytest

    from glidepath.app import PlanState

GOLDEN_PATH = Path(__file__).parent / "golden" / "cash_flow_example.csv"

RECORDED = datetime(2026, 8, 3, 12, 0, tzinfo=UTC)
TODAY = RECORDED.date()
PLAN_NAME = "Example"


def projected_state() -> PlanState:
    """A projected session over the launch example's household."""
    parsed = parse_facts_form(
        example_facts_form_data(), recorded_on=RECORDED, today=TODAY
    )
    assert parsed.household is not None
    return state_with_household(initial_plan_state(), parsed.household, today=TODAY)


class TestCashFlowCsvGolden:
    """The checked-in expected CSV, compared byte for byte."""

    def test_matches_checked_in_golden(self, request: pytest.FixtureRequest) -> None:
        """The export reproduces the reviewed golden bytes exactly."""
        text = cash_flow_csv(
            projected_state(), basis=ReportBasis.REAL, plan_name=PLAN_NAME
        )
        assert text is not None
        rendered = text.encode("utf-8")
        if request.config.getoption("--update-golden"):
            GOLDEN_PATH.parent.mkdir(exist_ok=True)
            GOLDEN_PATH.write_bytes(rendered)
        stored = GOLDEN_PATH.read_bytes()
        assert stored == rendered, (
            "cash-flow CSV bytes shifted — regenerate with `uv run --locked"
            " pytest --no-cov tests/test_export_golden.py --update-golden`,"
            " review the diff, and explain it in the pull request"
        )
