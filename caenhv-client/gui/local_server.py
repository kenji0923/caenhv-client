from __future__ import annotations

import json
import os

from PyQt5 import QtCore, QtNetwork

try:
    from ..communicator import get_server_name
except Exception:
    from communicator import get_server_name

_MAX_COMMAND_BYTES = 8192


class GuiLocalServer(QtCore.QObject):
    """QLocalServer accepting one-line commands; only show/raise are handled."""

    sig_show_requested = QtCore.pyqtSignal()

    def __init__(self, server_name: str | None = None, parent: QtCore.QObject | None = None) -> None:
        super().__init__(parent)
        self._name = get_server_name(server_name)
        self._buffers: dict[QtNetwork.QLocalSocket, bytes] = {}
        self._server = QtNetwork.QLocalServer(self)
        self._server.newConnection.connect(self._slot_new_connection)

    def server_name(self) -> str:
        return self._name

    def start(self) -> bool:
        QtNetwork.QLocalServer.removeServer(self._name)
        return self._server.listen(self._name)

    def stop(self) -> None:
        self._server.close()
        QtNetwork.QLocalServer.removeServer(self._name)

    def _slot_new_connection(self) -> None:
        while self._server.hasPendingConnections():
            sock = self._server.nextPendingConnection()
            if sock is None:
                break
            self._buffers[sock] = b""
            sock.readyRead.connect(lambda s=sock: self._slot_ready_read(s))
            sock.disconnected.connect(lambda s=sock: self._forget_socket(s))
            if sock.bytesAvailable() > 0:
                self._slot_ready_read(sock)

    def _slot_ready_read(self, sock: QtNetwork.QLocalSocket) -> None:
        if sock not in self._buffers:
            return
        buffer = self._buffers[sock] + bytes(sock.readAll())
        if len(buffer) > _MAX_COMMAND_BYTES:
            self._forget_socket(sock)
            sock.disconnectFromServer()
            return
        self._buffers[sock] = buffer
        if b"\n" not in buffer:
            return
        line = buffer.split(b"\n", 1)[0]
        token = line.decode("utf-8", errors="replace").strip().lower()
        self._forget_socket(sock)
        sock.disconnectFromServer()
        if token in ("show", "raise"):
            self.sig_show_requested.emit()

    def _forget_socket(self, sock: QtNetwork.QLocalSocket) -> None:
        self._buffers.pop(sock, None)


class GuiTcpShowServer(QtCore.QObject):
    """Optional TCP show-listener for remote fire/raise.

    Opt-in via CAENHV_CLIENT_TCP_PORT (CAENHV_CLIENT_TCP_BIND to restrict
    the interface, CAENHV_CLIENT_TCP_TOKEN for a shared token). Protocol:
    one line "show" or "show <token>"; the server replies "ok" only when
    the request is accepted, so remote callers can detect rejection. This
    channel can only show/raise the window — never change HV state.
    """

    sig_show_requested = QtCore.pyqtSignal()

    ENV_PORT = "CAENHV_CLIENT_TCP_PORT"
    ENV_BIND = "CAENHV_CLIENT_TCP_BIND"
    ENV_TOKEN = "CAENHV_CLIENT_TCP_TOKEN"

    def __init__(
        self,
        port: int,
        bind_address: str = "0.0.0.0",
        token: str = "",
        parent: QtCore.QObject | None = None,
        command_handler=None,
    ) -> None:
        super().__init__(parent)
        self._port = int(port)
        self._bind_address = str(bind_address)
        self._token = str(token)
        self._command_handler = command_handler
        self._buffers: dict[QtNetwork.QTcpSocket, bytes] = {}
        self._server = QtNetwork.QTcpServer(self)
        self._server.newConnection.connect(self._slot_new_connection)

    @classmethod
    def from_environment(
        cls, parent: QtCore.QObject | None = None, command_handler=None
    ) -> "GuiTcpShowServer | None":
        raw = os.environ.get(cls.ENV_PORT, "").strip()
        if not raw:
            return None
        try:
            port = int(raw)
        except ValueError:
            return None
        if port <= 0:
            return None
        bind = os.environ.get(cls.ENV_BIND, "").strip() or "0.0.0.0"
        token = os.environ.get(cls.ENV_TOKEN, "").strip()
        return cls(port, bind, token, parent=parent, command_handler=command_handler)

    def description(self) -> str:
        if self._token:
            return f"{self._bind_address}:{self._port} (token set: show + control)"
        return f"{self._bind_address}:{self._port} (no token: show/raise only)"

    def start(self) -> bool:
        return self._server.listen(QtNetwork.QHostAddress(self._bind_address), self._port)

    def stop(self) -> None:
        self._server.close()

    def _slot_new_connection(self) -> None:
        while self._server.hasPendingConnections():
            sock = self._server.nextPendingConnection()
            if sock is None:
                break
            self._buffers[sock] = b""
            sock.readyRead.connect(lambda s=sock: self._slot_ready_read(s))
            sock.disconnected.connect(lambda s=sock: self._buffers.pop(s, None))
            if sock.bytesAvailable() > 0:
                self._slot_ready_read(sock)

    def _slot_ready_read(self, sock: QtNetwork.QTcpSocket) -> None:
        if sock not in self._buffers:
            return
        buffer = self._buffers[sock] + bytes(sock.readAll())
        if len(buffer) > _MAX_COMMAND_BYTES:
            self._buffers.pop(sock, None)
            sock.disconnectFromHost()
            return
        self._buffers[sock] = buffer
        if b"\n" not in buffer:
            return
        line = buffer.split(b"\n", 1)[0]
        self._buffers.pop(sock, None)
        text = line.decode("utf-8", errors="replace").strip()

        reply: dict | None = None
        emit_show = False
        if text.startswith("{"):
            reply, emit_show = self._handle_command_line(text)
        else:
            # Bare "show [token]" text protocol (backward compatible).
            parts = text.split()
            command = parts[0].lower() if parts else ""
            provided = parts[1] if len(parts) > 1 else ""
            if command in ("show", "raise") and (not self._token or provided == self._token):
                emit_show = True
                reply = {"status": "ok"}

        if reply is not None:
            sock.write((json.dumps(reply, default=str) + "\n").encode("utf-8"))
            sock.flush()
        sock.disconnectFromHost()
        if emit_show:
            self.sig_show_requested.emit()

    def _handle_command_line(self, text: str) -> tuple[dict, bool]:
        """Parse and dispatch a JSON command; return (reply, emit_show)."""
        try:
            cmd = json.loads(text)
        except Exception:
            return {"status": "error", "error": "invalid JSON"}, False
        if not isinstance(cmd, dict) or "cmd" not in cmd:
            return {"status": "error", "error": "missing cmd"}, False
        name = str(cmd.get("cmd")).strip().lower()
        if name in ("show", "raise"):
            if self._token and cmd.get("token") != self._token:
                return {"status": "error", "error": "invalid token"}, False
            return {"status": "ok"}, True
        # Control commands: gated on a configured, matching token.
        if not self._token:
            return {"status": "error", "error": "remote control disabled: no token configured on GUI"}, False
        if cmd.get("token") != self._token:
            return {"status": "error", "error": "invalid token"}, False
        if self._command_handler is None:
            return {"status": "error", "error": "remote control not available"}, False
        try:
            result = self._command_handler(cmd)
        except Exception as exc:
            return {"status": "error", "error": str(exc)}, False
        return (result if isinstance(result, dict) else {"status": "ok"}), False
