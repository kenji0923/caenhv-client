"""Python interface to the caenhv-client GUI application.

This package lets an external Python process (e.g. a labscript BLACS tab or
worker) drive the standalone caenhv-client GUI:

- *fire* it — raise the window if running, else launch it (`fire_gui`,
  `notify_gui`), locally or to a remote host over TCP;
- *control HV* — set Vset/offset/power/params and read channels
  (`set_vset`, `set_power`, `set_param`, `set_offset`, `get_channel`,
  `send_command`). Control requests are executed by the GUI, so they pass
  through its channel-link engine and safeguards; the GUI is the single
  gateway to the devman server. Control requires a token configured on the
  GUI (`CAENHV_CLIENT_TCP_TOKEN`); without one the channel is show-only.

Local fire/raise uses a QLocalServer (named pipe on Windows, Unix socket on
POSIX); remote fire/control uses the GUI's opt-in TCP listener
(`CAENHV_CLIENT_TCP_PORT`). Stdlib-only on every platform; PyQt5 is an
optional local fallback transport.

The GUI itself is distributed as a standalone executable (PyInstaller). Set
``CAENHV_CLIENT_COMMAND`` to its full path if it is not on PATH.
"""

from __future__ import annotations

import json
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
ENV_REMOTE = "CAENHV_CLIENT_REMOTE"
ENV_REMOTE_TOKEN = "CAENHV_CLIENT_TCP_TOKEN"
SHOW_COMMAND = b"show\n"

__all__ = [
    "DEFAULT_SERVER_NAME",
    "ENV_LAUNCH_COMMAND",
    "ENV_REMOTE",
    "ENV_REMOTE_TOKEN",
    "ENV_SERVER_NAME",
    "RemoteClient",
    "SHOW_COMMAND",
    "default_launch_cmd",
    "default_popen_kwargs",
    "fire_gui",
    "get_channel",
    "get_imon",
    "get_link",
    "get_links",
    "get_offset",
    "get_param",
    "get_power",
    "get_server_name",
    "get_status",
    "get_vmon",
    "get_vset",
    "notify_gui",
    "send_command",
    "set_offset",
    "set_param",
    "set_power",
    "set_vset",
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


def _remote_from_env() -> tuple[str, int] | None:
    raw = os.environ.get(ENV_REMOTE, "").strip()
    if not raw or ":" not in raw:
        return None
    host, _, port_text = raw.rpartition(":")
    try:
        return host, int(port_text)
    except ValueError:
        return None


def _notify_via_tcp(host: str, port: int, token: str, timeout: float) -> bool:
    message = f"show {token}\n" if token else "show\n"
    try:
        with socket.create_connection((host, int(port)), timeout=timeout) as sock:
            sock.sendall(message.encode("utf-8"))
            sock.settimeout(timeout)
            # The GUI acknowledges accepted requests; rejection (e.g. bad
            # token) closes the connection without a reply.
            return sock.recv(8).startswith(b"ok")
    except OSError:
        return False


def notify_gui(
    server_name: str | None = None,
    *,
    timeout: float = 1.0,
    host: str | None = None,
    port: int | None = None,
    token: str | None = None,
) -> bool:
    """Deliver a show request to a running GUI. Return True if delivered.

    With host/port (or the CAENHV_CLIENT_REMOTE=host:port environment
    variable) the request goes over TCP to a GUI on another machine, which
    must have its remote show listener enabled (CAENHV_CLIENT_TCP_PORT).
    """
    if host is None and port is None:
        remote = _remote_from_env()
        if remote is not None:
            host, port = remote
    if host is not None or port is not None:
        if host is None or port is None:
            raise ValueError("both host and port are required for remote notify")
        if token is None:
            token = os.environ.get(ENV_REMOTE_TOKEN, "").strip()
        return _notify_via_tcp(host, int(port), token, timeout)
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
    host: str | None = None,
    port: int | None = None,
    token: str | None = None,
) -> str:
    """Raise the GUI if running, otherwise launch it detached.

    Returns "raised" or "launched". Raises TimeoutError if a freshly
    launched GUI does not start listening within launch_timeout, and
    RuntimeError if no launch command can be determined.

    Remote mode (host/port arguments or CAENHV_CLIENT_REMOTE): only
    raising is possible — a process cannot be launched on another machine.
    Keep the GUI auto-started at login on the remote host.
    """
    remote = (host, port) if (host is not None or port is not None) else _remote_from_env()
    if remote is not None:
        r_host, r_port = remote
        if notify_gui(host=r_host, port=r_port, token=token, timeout=connect_timeout):
            return "raised"
        raise RuntimeError(
            f"caenhv-client GUI not reachable at {r_host}:{r_port} — remote launch is not "
            "possible; the GUI must already be running there (enable CAENHV_CLIENT_TCP_PORT "
            "on the GUI host and auto-start it at login), and tokens must match"
        )
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


# --- Remote HV control (through the GUI gateway) ----------------------------
#
# Control commands are executed by the caenhv-client GUI on the target host,
# so they pass through its channel-link engine and safeguards. The GUI must
# have a token configured (CAENHV_CLIENT_TCP_TOKEN); without one it accepts
# only show/raise. Host/port/token default to CAENHV_CLIENT_REMOTE and
# CAENHV_CLIENT_TCP_TOKEN.


def send_command(
    cmd: dict,
    *,
    host: str | None = None,
    port: int | None = None,
    token: str | None = None,
    timeout: float = 2.0,
) -> dict:
    """Send one JSON control command to a remote GUI and return its reply.

    Raises RuntimeError if the GUI reports an error (bad token, control
    disabled, or a safeguard rejection such as exceeding SVMax).
    """
    if host is None and port is None:
        remote = _remote_from_env()
        if remote is not None:
            host, port = remote
    if host is None or port is None:
        raise ValueError("host and port are required (or set CAENHV_CLIENT_REMOTE=host:port)")
    if token is None:
        token = os.environ.get(ENV_REMOTE_TOKEN, "").strip()
    payload = dict(cmd)
    if token:
        payload["token"] = token
    line = (json.dumps(payload) + "\n").encode("utf-8")
    with socket.create_connection((host, int(port)), timeout=timeout) as sock:
        sock.sendall(line)
        sock.settimeout(timeout)
        buffer = b""
        while b"\n" not in buffer:
            chunk = sock.recv(4096)
            if not chunk:
                break
            buffer += chunk
    if not buffer.strip():
        raise RuntimeError(
            "no response from GUI (remote control may be disabled, or the token is wrong)"
        )
    reply = json.loads(buffer.split(b"\n", 1)[0].decode("utf-8"))
    if reply.get("status") != "ok":
        raise RuntimeError(reply.get("error", "remote command failed"))
    return reply


def get_channel(slot: int, ch: int, **kwargs) -> dict:
    """Return the remote channel's readings and settings."""
    return send_command({"cmd": "get", "slot": int(slot), "ch": int(ch)}, **kwargs)["values"]


def get_link(slot: int, ch: int, **kwargs) -> dict:
    """Return the channel's link relationship: {'reference', 'offset'}.

    'reference' is the 'slot:ch' string this channel is linked to (or None),
    'offset' its relative level in volts (or None when unlinked).
    """
    reply = send_command({"cmd": "get_link", "slot": int(slot), "ch": int(ch)}, **kwargs)
    return {"reference": reply.get("reference"), "offset": reply.get("offset")}


def get_offset(slot: int, ch: int, **kwargs):
    """Return the channel's link offset in volts, or None when unlinked."""
    return get_link(slot, ch, **kwargs)["offset"]


def get_links(**kwargs) -> dict:
    """Return every link as {'slot:ch': {'reference', 'offset'}}."""
    return send_command({"cmd": "get_links"}, **kwargs)["links"]


def set_vset(slot: int, ch: int, value: float, **kwargs) -> dict:
    return send_command({"cmd": "set_vset", "slot": int(slot), "ch": int(ch), "value": float(value)}, **kwargs)


def set_offset(slot: int, ch: int, value: float, **kwargs) -> dict:
    return send_command({"cmd": "set_offset", "slot": int(slot), "ch": int(ch), "value": float(value)}, **kwargs)


def set_power(slot: int, ch: int, on: bool, **kwargs) -> dict:
    return send_command({"cmd": "set_power", "slot": int(slot), "ch": int(ch), "on": bool(on)}, **kwargs)


def set_param(slot: int, ch: int, name: str, value, **kwargs) -> dict:
    """Set a channel parameter: rup, rdown, iset, trip, svmax, or pdown."""
    return send_command(
        {"cmd": "set_param", "slot": int(slot), "ch": int(ch), "name": str(name), "value": value}, **kwargs
    )


# --- Typed getters (mirror the setters) -------------------------------------
#
# Each does one round-trip and returns a single, typed value. Reading several
# fields at once? Call get_channel() once and index the dict instead.

_PARAM_KEYS = {"rup", "rdown", "rdwn", "iset", "trip", "svmax", "pdown", "label"}


def _read_field(slot: int, ch: int, key: str, kwargs: dict):
    values = get_channel(slot, ch, **kwargs)
    if key not in values:
        raise RuntimeError(
            f"channel {slot}:{ch} did not report '{key}' "
            "(the GUI could not read that parameter from the crate)"
        )
    return values[key]


def _as_bool(value) -> bool:
    if isinstance(value, bool):
        return value
    try:
        return bool(int(value))
    except (TypeError, ValueError):
        return str(value).strip().lower() in ("1", "true", "yes", "on")


def get_vset(slot: int, ch: int, **kwargs) -> float:
    """Vset setpoint in volts (signed by polarity)."""
    return float(_read_field(slot, ch, "vset", kwargs))


def get_vmon(slot: int, ch: int, **kwargs) -> float:
    """Measured output voltage in volts (signed by polarity)."""
    return float(_read_field(slot, ch, "vmon", kwargs))


def get_imon(slot: int, ch: int, **kwargs) -> float:
    """Measured output current (unsigned magnitude)."""
    return float(_read_field(slot, ch, "imon", kwargs))


def get_power(slot: int, ch: int, **kwargs) -> bool:
    """True when the channel is on."""
    return _as_bool(_read_field(slot, ch, "power", kwargs))


def get_status(slot: int, ch: int, **kwargs) -> int:
    """Raw CAEN Status bitmask (bit 0 ON, 6 external trip, 8 internal trip)."""
    return int(_read_field(slot, ch, "status", kwargs))


def get_param(slot: int, ch: int, name: str, **kwargs):
    """Read one parameter (mirrors set_param): rup, rdown, iset, trip, svmax, pdown, label.

    Numeric parameters return float; pdown and label return str.
    """
    key = str(name).strip().lower()
    if key == "rdwn":
        key = "rdown"
    if key not in _PARAM_KEYS:
        raise ValueError(f"unknown param '{name}'; expected one of {sorted(_PARAM_KEYS - {'rdwn'})}")
    value = _read_field(slot, ch, key, kwargs)
    return str(value) if key in ("pdown", "label") else float(value)


class RemoteClient:
    """Bound client for a caenhv-client GUI: set host/port/token once.

    >>> hv = RemoteClient("192.168.1.2", 50251, token="FanLabAdmin")
    >>> hv.set_vset(0, 0, 5.0)
    >>> hv.get_channel(0, 0)
    >>> hv.raise_window()

    All methods mirror the module-level functions but reuse the connection
    details. Safeguard rejections (e.g. exceeding SVMax) raise RuntimeError.
    """

    def __init__(self, host: str, port: int, token: str | None = None, *, timeout: float = 2.0) -> None:
        self.host = str(host)
        self.port = int(port)
        self.token = token if token is not None else os.environ.get(ENV_REMOTE_TOKEN, "").strip()
        self.timeout = float(timeout)

    @classmethod
    def from_env(cls, *, timeout: float = 2.0) -> "RemoteClient":
        """Build from CAENHV_CLIENT_REMOTE=host:port and CAENHV_CLIENT_TCP_TOKEN."""
        remote = _remote_from_env()
        if remote is None:
            raise ValueError(f"set {ENV_REMOTE}=host:port (or pass host/port explicitly)")
        host, port = remote
        return cls(host, port, timeout=timeout)

    def __repr__(self) -> str:
        gated = "token" if self.token else "no-token"
        return f"RemoteClient({self.host}:{self.port}, {gated})"

    def _kw(self) -> dict:
        return {"host": self.host, "port": self.port, "token": self.token, "timeout": self.timeout}

    def send_command(self, cmd: dict) -> dict:
        return send_command(cmd, **self._kw())

    def get_channel(self, slot: int, ch: int) -> dict:
        return get_channel(slot, ch, **self._kw())

    def set_vset(self, slot: int, ch: int, value: float) -> dict:
        return set_vset(slot, ch, value, **self._kw())

    def set_offset(self, slot: int, ch: int, value: float) -> dict:
        return set_offset(slot, ch, value, **self._kw())

    def set_power(self, slot: int, ch: int, on: bool) -> dict:
        return set_power(slot, ch, on, **self._kw())

    def set_param(self, slot: int, ch: int, name: str, value) -> dict:
        return set_param(slot, ch, name, value, **self._kw())

    def get_vset(self, slot: int, ch: int) -> float:
        return get_vset(slot, ch, **self._kw())

    def get_vmon(self, slot: int, ch: int) -> float:
        return get_vmon(slot, ch, **self._kw())

    def get_imon(self, slot: int, ch: int) -> float:
        return get_imon(slot, ch, **self._kw())

    def get_power(self, slot: int, ch: int) -> bool:
        return get_power(slot, ch, **self._kw())

    def get_status(self, slot: int, ch: int) -> int:
        return get_status(slot, ch, **self._kw())

    def get_param(self, slot: int, ch: int, name: str):
        return get_param(slot, ch, name, **self._kw())

    def get_link(self, slot: int, ch: int) -> dict:
        return get_link(slot, ch, **self._kw())

    def get_offset(self, slot: int, ch: int):
        return get_offset(slot, ch, **self._kw())

    def get_links(self) -> dict:
        return get_links(**self._kw())

    def raise_window(self) -> bool:
        """Raise the GUI window if reachable (no launch). Returns success."""
        return notify_gui(host=self.host, port=self.port, token=self.token, timeout=self.timeout)
