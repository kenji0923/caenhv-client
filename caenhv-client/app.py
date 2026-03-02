from __future__ import annotations

import sys
from pathlib import Path

from PyQt5 import QtGui, QtWidgets

try:
    from .gui.standalone_window import StandaloneMainWindow
except Exception:
    from gui.standalone_window import StandaloneMainWindow


def main() -> int:
    app = QtWidgets.QApplication(sys.argv)
    root_dir = Path(__file__).resolve().parents[1]
    icon_path = Path(__file__).resolve().parent / "resources" / "caenhv-client.svg"
    if icon_path.exists():
        app_icon = QtGui.QIcon(str(icon_path))
        app.setWindowIcon(app_icon)
    else:
        app_icon = QtGui.QIcon()
    window = StandaloneMainWindow(root_dir)
    if not app_icon.isNull():
        window.setWindowIcon(app_icon)
    window.show()
    return int(app.exec_())


if __name__ == "__main__":
    raise SystemExit(main())
