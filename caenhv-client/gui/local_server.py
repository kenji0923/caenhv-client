from __future__ import annotations

import os

from PyQt5 import QtCore, QtNetwork

try:
    from ..communicator import get_server_name
except Exception:
    from communicator import get_server_name

_MAX_COMMAND_BYTES = 1024


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
    ) -> None:
        super().__init__(parent)
        self._port = int(port)
        self._bind_address = str(bind_address)
        self._token = str(token)
        self._buffers: dict[QtNetwork.QTcpSocket, bytes] = {}
        self._server = QtNetwork.QTcpServer(self)
        self._server.newConnection.connect(self._slot_new_connection)

    @classmethod
    def from_environment(cls, parent: QtCore.QObject | None = None) -> "GuiTcpShowServer | None":
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
        return cls(port, bind, token, parent=parent)

    def description(self) -> str:
        suffix = " (token required)" if self._token else ""
        return f"{self._bind_address}:{self._port}{suffix}"

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
        parts = line.decode("utf-8", errors="replace").strip().split()
        command = parts[0].lower() if parts else ""
        provided_token = parts[1] if len(parts) > 1 else ""
        self._buffers.pop(sock, None)
        accepted = command in ("show", "raise") and (not self._token or provided_token == self._token)
        if accepted:
            sock.write(b"ok\n")
            sock.flush()
        sock.disconnectFromHost()
        if accepted:
            self.sig_show_requested.emit()
