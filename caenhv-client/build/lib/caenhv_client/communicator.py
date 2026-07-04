"""Compatibility shim: the fire/raise interface lives in caenhv-client-python.

The implementation is the separately published ``caenhv_client_python``
package (the thin PyPI interface used by BLACS and other Python projects).
This shim keeps ``caenhv_client.communicator`` imports working inside the
application, resolving the sibling source checkout when the package is not
installed (source runs and PyInstaller builds both work).
"""

from __future__ import annotations

import sys
from pathlib import Path

try:
    from caenhv_client_python import (  # noqa: F401
        DEFAULT_SERVER_NAME,
        ENV_LAUNCH_COMMAND,
        ENV_SERVER_NAME,
        SHOW_COMMAND,
        default_launch_cmd,
        default_popen_kwargs,
        fire_gui,
        get_server_name,
        notify_gui,
    )
except ImportError:
    _src = Path(__file__).resolve().parents[1] / "caenhv-client-python" / "src"
    if _src.is_dir():
        sys.path.insert(0, str(_src))
    from caenhv_client_python import (  # noqa: F401
        DEFAULT_SERVER_NAME,
        ENV_LAUNCH_COMMAND,
        ENV_SERVER_NAME,
        SHOW_COMMAND,
        default_launch_cmd,
        default_popen_kwargs,
        fire_gui,
        get_server_name,
        notify_gui,
    )
