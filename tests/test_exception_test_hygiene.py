"""Guard: every ``pytest.raises`` block holds exactly one invocation.

SonarQube rule python:S5778 requires the body of a ``with
pytest.raises(...):`` block to contain exactly one invocation that
could raise — if an argument constructor raises the expected type, the
test passes without exercising the code under test. This guard walks
the AST of every test module so a violation fails ``make check``
locally instead of surfacing as a SonarCloud issue on the PR: any call
expression in an argument position (even a "trivial" ``Decimal(0)`` or
``date(...)``) must be hoisted to a local variable above the block.
"""

import ast
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Iterator

TESTS_DIR = Path(__file__).parent


def _is_pytest_raises(expression: ast.expr) -> bool:
    """Whether ``expression`` is a ``pytest.raises(...)`` context manager."""
    return (
        isinstance(expression, ast.Call)
        and isinstance(expression.func, ast.Attribute)
        and expression.func.attr == "raises"
        and isinstance(expression.func.value, ast.Name)
        and expression.func.value.id == "pytest"
    )


def _raises_blocks(tree: ast.Module) -> Iterator[ast.With]:
    """Every ``with`` statement managed by ``pytest.raises``."""
    for node in ast.walk(tree):
        if isinstance(node, ast.With) and any(
            _is_pytest_raises(item.context_expr) for item in node.items
        ):
            yield node


def _invocations_in_body(block: ast.With) -> int:
    """The number of call expressions inside the block's body."""
    return sum(
        isinstance(node, ast.Call)
        for statement in block.body
        for node in ast.walk(statement)
    )


def test_every_pytest_raises_block_has_exactly_one_invocation() -> None:
    """One call, variable-only arguments — the S5778 contract, enforced."""
    violations: list[str] = []
    for path in sorted(TESTS_DIR.glob("test_*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        violations.extend(
            f"{path.name}:{block.lineno} has {_invocations_in_body(block)}"
            " invocations inside pytest.raises (hoist arguments above the block)"
            for block in _raises_blocks(tree)
            if _invocations_in_body(block) != 1
        )
    assert not violations, "\n".join(violations)
