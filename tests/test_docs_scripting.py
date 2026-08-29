"""The scripting guide's code blocks run against the shipped package.

``docs/scripting.md`` is the one place that documents driving the
engine from a script. The module layout is not a stable API (planning
§4.10), so the guide would rot silently as names move; instead every
```python fence in it is executed here, in order, in one namespace —
the guide is written as a single cumulative script — from a temporary
working directory, so the plan file and CSV it writes never land in
the checkout.
"""

import re
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import pytest

GUIDE = Path(__file__).resolve().parents[1] / "docs" / "scripting.md"
_PYTHON_FENCE = re.compile(r"^```python\n(.*?)^```$", re.MULTILINE | re.DOTALL)
_MIN_BLOCKS = 5


def python_blocks() -> list[str]:
    """Every ```python fence in the guide, in document order."""
    return _PYTHON_FENCE.findall(GUIDE.read_text(encoding="utf-8"))


class TestScriptingGuide:
    """The guide is executable end to end."""

    def test_guide_has_python_blocks(self) -> None:
        """A regex that matched nothing would make the run test vacuous."""
        assert len(python_blocks()) >= _MIN_BLOCKS

    def test_blocks_run_in_order(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Each block runs on the namespace the earlier ones built."""
        monkeypatch.chdir(tmp_path)
        namespace: dict[str, object] = {"__name__": "scripting_guide"}
        for index, block in enumerate(python_blocks(), start=1):
            code = compile(block, f"{GUIDE.name}#block{index}", "exec")
            # The guide's code is repository content under test, not input.
            exec(code, namespace)  # noqa: S102
        assert (tmp_path / "my-plan.glidepath.json").is_file()
        assert (tmp_path / "my-plan-cash-flow.csv").is_file()
