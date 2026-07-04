"""Fire/raise interface for the caenhv-client GUI.

This module lets an external process (e.g. a BLACS tab or worker in another
project) *fire* the standalone caenhv-client GUI: raise the window if the app
is already running, or launch it otherwise. This is deliberately the only
remote capability — there is no remote control of HV settings.

The GUI listens on a QLocalServer (a named pipe on Windows, a Unix socket
under the temp directory on POSIX). The protocol is a single newline-
terminated UTF-8 token: ``show`` (``raise`` is accepted as an alias).

This module is importable with the standard library only. Talking to the
server uses PyQt5's QLocalSocket when available; on POSIX a plain AF_UNIX
socket is used as a fallback, so no Qt is required in the calling
environment. On Windows PyQt5 must be importable (BLACS installs have it).
"""

from __future__ import annotations

import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time

DEFAULT_SERVER_NAME = "caenhv-client"
ENV_SERVER_NAME = "CAENHV_CLIENT_IPC_NAME"
SHOW_COMMAND = b"show\n"

_qt_app = None


def get_server_name(server_name: str | None = None) -> str:
    if server_name:
        return server_name
    return os.environ.get(ENV_SERVER_NAME) or DEFAULT_SERVER_NAME


def _notify_via_qlocalsocket(name: str, timeout: float) -> bool:
    from PyQt5 import QtCore, QtNetwork

    global _qt_app
    if QtCore.QCoreApplication.instance() is None:
        _qt_app = QtCore.QCoreApplication([])
    sock = QtNetwork.QLocalSocket()
    sock.connectToServer(name)
    try:
        if not sock.waitForConnected(int(timeout * 1000)):
            return False
        sock.write(SHOW_COMMAND)
        if not sock.waitForBytesWritten(int(timeout * 1000)):
            return False
        sock.disconnectFromServer()
        return True
    finally:
        sock.abort()


def _notify_via_unix_socket(name: str, timeout: float) -> bool:
    # QLocalServer places its Unix socket in the temp dir; both Qt and
    # tempfile honor TMPDIR, so the paths agree.
    path = os.path.join(tempfile.gettempdir(), name)
    sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    sock.settimeout(timeout)
    try:
        sock.connect(path)
        sock.sendall(SHOW_COMMAND)
        return True
    except OSError:
        return False
    finally:
        sock.close()


def notify_gui(server_name: str | None = None, *, timeout: float = 1.0) -> bool:
    """Deliver a show request to a running GUI. Return True if delivered."""
    name = get_server_name(server_name)
    try:
        return _notify_via_qlocalsocket(name, timeout)
    except ImportError:
        if os.name == "posix":
            return _notify_via_unix_socket(name, timeout)
        raise


def default_launch_cmd() -> list[str]:
    for script in ("caenhv-client-gui", "caenhv-client"):
        found = shutil.which(script)
        if found:
            return [found]
    return [sys.executable, "-m", "caenhv_client"]


def default_popen_kwargs() -> dict:
    kwargs: dict = {
        "stdin": subprocess.DEVNULL,
        "stdout": subprocess.DEVNULL,
        "stderr": subprocess.DEVNULL,
        "close_fds": True,
    }
    if os.name == "nt":
        kwargs["creationflags"] = (
            subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
        )
    else:
        kwargs["start_new_session"] = True
    return kwargs


def fire_gui(
    server_name: str | None = None,
    *,
    launch_cmd: list[str] | None = None,
    connect_timeout: float = 1.0,
    launch_timeout: float = 15.0,
) -> str:
    """Raise the GUI if running, otherwise launch it detached.

    Returns "raised" or "launched". Raises TimeoutError if a freshly
    launched GUI does not start listening within launch_timeout.
    """
    if notify_gui(server_name, timeout=connect_timeout):
        return "raised"
    cmd = launch_cmd or default_launch_cmd()
    subprocess.Popen(cmd, **default_popen_kwargs())
    deadline = time.monotonic() + launch_timeout
    while time.monotonic() < deadline:
        if notify_gui(server_name, timeout=connect_timeout):
            return "launched"
        time.sleep(0.25)
    raise TimeoutError(f"caenhv-client GUI did not start within {launch_timeout} s (cmd: {cmd})")
