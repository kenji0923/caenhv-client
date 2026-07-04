from __future__ import annotations

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
