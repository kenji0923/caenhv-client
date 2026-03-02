from __future__ import annotations

import sys
from pathlib import Path

from PyQt5 import QtGui, QtWidgets

try:
    from .gui.standalone_window import StandaloneMainWindow
except Exception:
    from gui.standalone_window import StandaloneMainWindow


def main() -> int:
    try:
        from desktop_app import set_process_appid

        set_process_appid("caenhv_client")
    except Exception:
        pass
    app = QtWidgets.QApplication(sys.argv)
    root_dir = Path(__file__).resolve().parents[1]
    resources_dir = Path(__file__).resolve().parent / "resources"
    app_icon = QtGui.QIcon()
    for icon_name in (
        "caenhv-client.ico",
        "caenhv-client_16.png",
        "caenhv-client_32.png",
        "caenhv-client_48.png",
        "caenhv-client_256.png",
        "caenhv-client.svg",
    ):
        icon_path = resources_dir / icon_name
        if icon_path.exists():
            app_icon.addFile(str(icon_path))
    if not app_icon.isNull():
        app.setWindowIcon(app_icon)
    window = StandaloneMainWindow(root_dir)
    if not app_icon.isNull():
        window.setWindowIcon(app_icon)
    window.show()
    return int(app.exec_())


if __name__ == "__main__":
    raise SystemExit(main())
