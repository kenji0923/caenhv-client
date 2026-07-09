"""Tests for the pure-Python RemoteClient / send_command layer.

No hardware and no PyQt: a fake in-process TCP server speaks the same
newline-delimited JSON protocol as the caenhv-client GUI listener.
"""

from __future__ import annotations

import json
import socket
import threading

import pytest

import caenhv_client_python as cc
from caenhv_client_python import RemoteClient, send_command


class FakeGuiServer:
    """In-process TCP server speaking the newline-JSON control protocol.

    ``handler(cmd, conn_index) -> reply`` returns the dict to send back, or the
    sentinel ``FakeGuiServer.CLOSE`` to drop the connection without replying
    (mimicking a closed socket / disabled control). ``conn_index`` is the
    0-based accept order, so a handler can behave differently per connection.
    """

    CLOSE = object()

    def __init__(self, handler):
        self._handler = handler
        self._srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._srv.bind(("127.0.0.1", 0))
        self._srv.listen(8)
        self.host, self.port = self._srv.getsockname()
        self.commands: list[dict] = []
        self.connections = 0
        self._stop = False
        self._thread = threading.Thread(target=self._serve, daemon=True)
        self._thread.start()

    def _serve(self) -> None:
        while not self._stop:
            try:
                conn, _ = self._srv.accept()
            except OSError:
                return
            index = self.connections
            self.connections += 1
            threading.Thread(
                target=self._handle_conn, args=(conn, index), daemon=True
            ).start()

    def _handle_conn(self, conn, index) -> None:
        buffer = b""
        with conn:
            while not self._stop:
                try:
                    chunk = conn.recv(4096)
                except OSError:
                    return
                if not chunk:
                    return
                buffer += chunk
                while b"\n" in buffer:
                    line, buffer = buffer.split(b"\n", 1)
                    if not line.strip():
                        continue
                    cmd = json.loads(line.decode("utf-8"))
                    self.commands.append(cmd)
                    reply = self._handler(cmd, index)
                    if reply is self.CLOSE:
                        return  # drop without replying
                    conn.sendall((json.dumps(reply) + "\n").encode("utf-8"))

    def close(self) -> None:
        self._stop = True
        try:
            self._srv.close()
        except OSError:
            pass


@pytest.fixture
def server_factory():
    servers: list[FakeGuiServer] = []

    def make(handler):
        srv = FakeGuiServer(handler)
        servers.append(srv)
        return srv

    yield make
    for srv in servers:
        srv.close()


def _client(srv, **kw):
    return RemoteClient(srv.host, srv.port, token="tok", timeout=1.0, **kw)


# --- send_command basics ---------------------------------------------------

def test_send_command_ok(server_factory):
    srv = server_factory(lambda cmd, i: {"status": "ok", "echo": cmd.get("cmd")})
    reply = send_command({"cmd": "ping"}, host=srv.host, port=srv.port, token="tok", timeout=1.0)
    assert reply["status"] == "ok"
    assert reply["echo"] == "ping"
    # The token is injected into the wire payload.
    assert srv.commands[0]["token"] == "tok"


def test_error_reply_raises_runtimeerror(server_factory):
    srv = server_factory(lambda cmd, i: {"status": "error", "error": "SVMax exceeded"})
    with pytest.raises(RuntimeError) as ei:
        send_command({"cmd": "set_vset"}, host=srv.host, port=srv.port, token="tok", timeout=1.0)
    assert "SVMax exceeded" in str(ei.value)


def test_error_reply_with_channel_is_surfaced(server_factory):
    srv = server_factory(
        lambda cmd, i: {"status": "error", "error": "resulted Vset out of range", "channel": "1:3"}
    )
    with pytest.raises(RuntimeError) as ei:
        _client(srv).set_linked_bulk([{"slot": 1, "ch": 3, "vset": 9e9}])
    assert ei.value.channel == "1:3"
    assert "1:3" in str(ei.value)
    assert "out of range" in str(ei.value)


def test_empty_reply_raises(server_factory):
    # Connection closed with no reply (control disabled / wrong token).
    srv = server_factory(lambda cmd, i: FakeGuiServer.CLOSE)
    with pytest.raises((ConnectionError, RuntimeError)) as ei:
        send_command({"cmd": "get"}, host=srv.host, port=srv.port, token="tok", timeout=1.0)
    assert "no response" in str(ei.value)


# --- get_many parsing ------------------------------------------------------

def test_get_many_parsing_with_error_and_errors(server_factory):
    values = [
        {"vmon": 1.0, "vset": 2.0, "imon": 0.1, "power": 1, "status": 1},
        {"error": "channel 0:9 could not be read"},
        # Partial: vset absent, reason under errors; readable keys present.
        {"vmon": 3.0, "imon": 0.2, "status": 1, "errors": {"vset": "read timeout"}},
    ]
    srv = server_factory(lambda cmd, i: {"status": "ok", "values": values})
    out = _client(srv).get_many([(0, 0), (0, 9), (0, 1)])
    assert out[0]["vset"] == 2.0
    assert out[1]["error"] == "channel 0:9 could not be read"
    # New errors sub-dict passes through unchanged; the failed key stays absent.
    assert "vset" not in out[2]
    assert out[2]["errors"]["vset"] == "read timeout"
    assert out[2]["vmon"] == 3.0
    # include_link flag is carried on the wire.
    assert srv.commands[0]["include_link"] is False


# --- persistent connection semantics --------------------------------------

def test_persistent_reuses_one_connection(server_factory):
    srv = server_factory(lambda cmd, i: {"status": "ok", "n": i})
    with _client(srv, persistent=True) as hv:
        r1 = hv.send_command({"cmd": "a"})
        r2 = hv.send_command({"cmd": "b"})
        r3 = hv.send_command({"cmd": "c"})
    # All three replies came from the same (first) accepted connection.
    assert r1["n"] == r2["n"] == r3["n"] == 0
    assert srv.connections == 1
    assert len(srv.commands) == 3


def test_persistent_reconnects_after_drop(server_factory):
    # First connection drops without replying; the client must reconnect.
    def handler(cmd, i):
        if i == 0:
            return FakeGuiServer.CLOSE
        return {"status": "ok", "conn": i}

    srv = server_factory(handler)
    with _client(srv, persistent=True) as hv:
        with pytest.raises(RuntimeError):
            hv.send_command({"cmd": "first"})
        reply = hv.send_command({"cmd": "second"})
    assert reply["conn"] == 1
    assert srv.connections == 2


def test_non_persistent_opens_connection_per_call(server_factory):
    srv = server_factory(lambda cmd, i: {"status": "ok"})
    hv = _client(srv, persistent=False)
    hv.send_command({"cmd": "a"})
    hv.send_command({"cmd": "b"})
    assert srv.connections == 2


def test_module_get_many_helper(server_factory):
    values = [{"vmon": 1.0, "vset": 2.0, "imon": 0.0, "power": 0, "status": 0}]
    srv = server_factory(lambda cmd, i: {"status": "ok", "values": values})
    out = cc.get_many([(0, 0)], host=srv.host, port=srv.port, token="tok", timeout=1.0)
    assert out[0]["vmon"] == 1.0
