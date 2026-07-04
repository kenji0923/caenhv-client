"""Python interface to the caenhv-client GUI application.

This package lets an external Python process (e.g. a labscript BLACS tab or
worker) *fire* the standalone caenhv-client GUI: raise the window if the app
is already running, or launch it otherwise. This is deliberately the only
remote capability — there is no remote control of HV settings.

The GUI listens on a QLocalServer (named pipe ``\\\\.\\pipe\\<name>`` on
Windows, Unix socket under the temp directory on POSIX). The protocol is a
single newline-terminated UTF-8 token: ``show`` (``raise`` is accepted as an
alias). Stdlib-only on every platform; PyQt5 is used as a fallback transport
only if it happens to be importable.

The GUI itself is distributed as a standalone executable (PyInstaller). Set
the ``CAENHV_CLIENT_COMMAND`` environment variable to its full path (or a
command line) if it is not on PATH.
"""

from __future__ import annotations

import os
import shlex
import shutil
import socket
import subprocess
import sys
import tempfile
import time

DEFAULT_SERVER_NAME = "caenhv-client"
ENV_SERVER_NAME = "CAENHV_CLIENT_IPC_NAME"
ENV_LAUNCH_COMMAND = "CAENHV_CLIENT_COMMAND"
SHOW_COMMAND = b"show\n"

__all__ = [
    "DEFAULT_SERVER_NAME",
    "ENV_LAUNCH_COMMAND",
    "ENV_SERVER_NAME",
    "SHOW_COMMAND",
    "default_launch_cmd",
    "default_popen_kwargs",
    "fire_gui",
    "get_server_name",
    "notify_gui",
]

_qt_app = None


def get_server_name(server_name: str | None = None) -> str:
    if server_name:
        return server_name
    return os.environ.get(ENV_SERVER_NAME) or DEFAULT_SERVER_NAME


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


def _notify_via_windows_pipe(name: str) -> bool:
    # QLocalServer pipes are file-openable; a plain write delivers the token.
    try:
        with open(rf"\\.\pipe\{name}", "wb", buffering=0) as pipe:
            pipe.write(SHOW_COMMAND)
        return True
    except OSError:
        return False


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


def notify_gui(server_name: str | None = None, *, timeout: float = 1.0) -> bool:
    """Deliver a show request to a running GUI. Return True if delivered."""
    name = get_server_name(server_name)
    if os.name == "posix":
        if _notify_via_unix_socket(name, timeout):
            return True
    elif os.name == "nt":
        if _notify_via_windows_pipe(name):
            return True
    try:
        return _notify_via_qlocalsocket(name, timeout)
    except ImportError:
        return False


def default_launch_cmd() -> list[str] | None:
    configured = os.environ.get(ENV_LAUNCH_COMMAND, "").strip()
    if configured:
        return shlex.split(configured, posix=(os.name != "nt"))
    for script in ("caenhv-client-gui", "caenhv-client"):
        found = shutil.which(script)
        if found:
            return [found]
    try:
        import caenhv_client  # noqa: F401  (source/pip install present)
    except ImportError:
        return None
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
    launched GUI does not start listening within launch_timeout, and
    RuntimeError if no launch command can be determined.
    """
    if notify_gui(server_name, timeout=connect_timeout):
        return "raised"
    cmd = launch_cmd or default_launch_cmd()
    if not cmd:
        raise RuntimeError(
            "caenhv-client GUI is not running and no launch command was found; "
            f"set {ENV_LAUNCH_COMMAND} to the caenhv-client executable path"
        )
    subprocess.Popen(cmd, **default_popen_kwargs())
    deadline = time.monotonic() + launch_timeout
    while time.monotonic() < deadline:
        if notify_gui(server_name, timeout=connect_timeout):
            return "launched"
        time.sleep(0.25)
    raise TimeoutError(f"caenhv-client GUI did not start within {launch_timeout} s (cmd: {cmd})")
