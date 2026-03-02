from __future__ import annotations

import sys
from pathlib import Path

from PyQt5 import QtWidgets

try:
    from .gui.standalone_window import StandaloneMainWindow
except Exception:
    from gui.standalone_window import StandaloneMainWindow


def main() -> int:
    app = QtWidgets.QApplication(sys.argv)
    root_dir = Path(__file__).resolve().parents[1]
    window = StandaloneMainWindow(root_dir)
    window.show()
    return int(app.exec_())


if __name__ == "__main__":
    raise SystemExit(main())
