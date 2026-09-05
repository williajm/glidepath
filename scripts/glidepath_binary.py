"""Nuitka entry point, with worker dispatch before any Qt imports."""

import multiprocessing
import sys
from pathlib import Path


def main() -> None:
    """Launch the app, or explicitly run packaging checks in a temporary session."""
    multiprocessing.freeze_support()
    match sys.argv:
        case [_, "--smoke-test", report]:
            from binary_smoke import (  # noqa: PLC0415 — import after worker dispatch.
                run_smoke_test,
            )

            run_smoke_test(Path(report))
        case _:
            from glidepath.gui.main import (  # noqa: PLC0415 — import after worker dispatch.
                main as gui_main,
            )

            gui_main()


if __name__ == "__main__":
    main()
