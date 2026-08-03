"""Shared pytest configuration for the glidepath test suite."""

# Runtime import required: pytest resolves hook signatures by evaluating
# their annotations, so a TYPE_CHECKING-only import raises NameError.
import pytest  # noqa: TC002


def pytest_addoption(parser: pytest.Parser) -> None:
    """Register the golden-output regeneration flag (roadmap 4.5).

    ``--update-golden`` rewrites checked-in golden outputs from the
    current engine instead of only comparing against them. The diff it
    produces must be reviewed and explained in the pull request.
    """
    parser.addoption(
        "--update-golden",
        action="store_true",
        default=False,
        help="Rewrite checked-in golden outputs before comparing.",
    )
