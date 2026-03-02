from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(slots=True)
class ConnectionState:
    server_host: str = "127.0.0.1"
    device_host: str = ""
    client_name: str = ""
    connected: bool = False


@dataclass(slots=True)
class ChannelState:
    slot: int
    channel: int
    label: str = ""
    vset: float = 0.0
    vmon: float = 0.0
    imon_uA: float = 0.0
    enabled: bool = False
    status: str = "UNKNOWN"
    reference: str = "None"
    reference_offset: float = 0.0
    rup: float = 0.0
    rdown: float = 0.0
    trip: float = 0.0
    svmax: float = 0.0
    pdown_mode: str = "RAMP"


@dataclass(slots=True)
class LinkRule:
    source: tuple[int, int]
    reference: tuple[int, int]
    delta_v: float


@dataclass(slots=True)
class ResourceRow:
    slot: int
    board: str
    channel: int | None
    owner: str | None
    resource: str


@dataclass(slots=True)
class AppState:
    connection: ConnectionState = field(default_factory=ConnectionState)
    channels: dict[tuple[int, int], ChannelState] = field(default_factory=dict)
    links: list[LinkRule] = field(default_factory=list)
    resources: list[ResourceRow] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)
