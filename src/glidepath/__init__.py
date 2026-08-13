"""glidepath: a desktop retirement and investment planner (UK-first).

``[project] version`` in pyproject.toml is the single version source
(rewritten only by ``make bump``, planning §4.10); it reaches the
installed distribution as metadata, and ``__version__`` merely reads
it back — never state a version here.
"""

from importlib.metadata import version

__version__ = version("glidepath")
