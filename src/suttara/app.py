"""Application entry point for Suttara."""

from __future__ import annotations

import sys

from .ui.main_window import MainWindow, create_application


def main(argv: list[str] | None = None) -> int:
    """Create and run the Suttara desktop application."""

    app = create_application(list(sys.argv if argv is None else argv))
    window = MainWindow()
    window.show()
    return app.exec()


__all__ = ["MainWindow", "main"]
