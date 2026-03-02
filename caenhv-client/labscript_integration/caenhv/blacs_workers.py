"""BLACS worker for CAEN HV devman client."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import h5py
import labscript_utils.h5_lock  # noqa: F401
from blacs.tab_base_classes import Worker

try:
    from ...worker.client_worker import ClientWorker
except Exception:
    from caenhv_client.worker.client_worker import ClientWorker


class CAENHVWorker(Worker):
    _CONN_RE = re.compile(r"^slot(?P<slot>\d+)_ch(?P<ch>\d+)_(?P<field>[A-Za-z0-9_]+)$")
    _FIELD_TO_PARAM = {
        "vset": "V0Set",
        "iset": "I0Set",
        "rup": "RUp",
        "rdown": "RDWn",
        "trip": "Trip",
        "svmax": "SVMax",
        "pdown": "PDWN",
    }

    def init(self) -> None:
        paths = []
        for raw in list(getattr(self, "bridge_search_paths", []) or []):
            if str(raw).strip():
                paths.append(Path(str(raw)))
        self._client = ClientWorker(bridge_search_paths=paths or None)
        self._connected = False
        self._channels = {(int(s), int(c)) for s, c in list(getattr(self, "channels", []) or [])}

    def shutdown(self) -> None:
        self.disconnect_client()

    def connect_client(
        self,
        server_host: str | None = None,
        server_port: int | None = None,
        client_name: str | None = None,
        force: bool | None = None,
    ) -> dict[str, Any]:
        host = str(server_host or getattr(self, "server_host", "127.0.0.1"))
        port = int(server_port if server_port is not None else getattr(self, "server_port", 50250))
        name = str(client_name or getattr(self, "client_name", f"blacs_{self.device_name}"))
        force_connect = bool(force if force is not None else getattr(self, "force_connect", False))
        if self._connected:
            if host == str(getattr(self, "server_host", host)) and port == int(
                getattr(self, "server_port", port)
            ) and name == str(getattr(self, "client_name", name)):
                return {"connected": True, "server_host": host, "server_port": port, "client_name": name}
            self.disconnect_client()
        payload = self._client.connect_client(
            server_host=host,
            server_port=port,
            client_name=name,
            force=force_connect,
        )
        self.server_host = host
        self.server_port = port
        self.client_name = name
        self._connected = True
        return payload

    def disconnect_client(self) -> bool:
        if not self._connected:
            return True
        self._client.disconnect_client()
        self._connected = False
        return True

    def refresh_resources(self) -> list[dict[str, Any]]:
        return self._client.refresh_resources_cached()

    def refresh_channel_snapshot(self, slot: int, channel: int) -> dict[str, Any]:
        return self._client.refresh_channel_snapshot(int(slot), int(channel))

    def fetch_channel_settings(self, slot: int, channel: int) -> dict[str, Any]:
        return self._client.fetch_channel_settings(int(slot), int(channel))

    def acquire_resource(self, resource: str) -> bool:
        return self._client.acquire_resource(str(resource))

    def release_resource(self, resource: str) -> bool:
        return self._client.release_resource(str(resource))

    def set_channel_name(self, slot: int, channel: int, label: str) -> bool:
        self._client.set_channel_name(int(slot), int(channel), str(label))
        return True

    def set_link_rule(self, slot: int, channel: int, reference: str, offset: float) -> dict[str, float] | None:
        ref = str(reference).strip()
        if not ref or ref.lower() == "none":
            self._client.set_link_rule(int(slot), int(channel), None, 0.0, sync_ramps=False)
            return None
        if ":" not in ref:
            raise RuntimeError(f"invalid reference format: {reference}")
        ref_slot_s, ref_ch_s = ref.split(":", 1)
        return self._client.set_link_rule(
            int(slot),
            int(channel),
            (int(ref_slot_s), int(ref_ch_s)),
            float(offset),
            sync_ramps=True,
        )

    def set_reference_offset(self, slot: int, channel: int, offset: float) -> bool:
        self._client.apply_linked_offset(int(slot), int(channel), float(offset))
        return True

    def apply_vset(self, slot: int, channel: int, value: float) -> bool:
        self._client.apply_linked_vset(int(slot), int(channel), float(value))
        return True

    def set_channel_power(self, slot: int, channel: int, enabled: bool) -> bool:
        self._client.set_channel_param(int(slot), int(channel), "Pw", 1 if bool(enabled) else 0)
        return True

    def set_channel_param(self, slot: int, channel: int, name: str, value: Any) -> bool:
        self._client.set_channel_param(int(slot), int(channel), str(name), value)
        return True

    def check_remote_values(self) -> dict[str, Any]:
        values: dict[str, Any] = {}
        for slot, channel in sorted(self._channels):
            settings = self._client.fetch_channel_settings(int(slot), int(channel))
            for field in ("vset", "iset", "rup", "rdown", "trip", "svmax", "power"):
                if field in settings:
                    values[f"slot{slot}_ch{channel}_{field if field != 'power' else 'enable'}"] = settings[field]
        return values

    def program_manual(self, values: dict[str, Any], force: bool = False) -> dict[str, Any]:
        _ = force
        for connection, value in values.items():
            parsed = self._parse_connection(connection)
            if parsed is None:
                continue
            slot, channel, field = parsed
            if field == "enable":
                self.set_channel_power(slot, channel, bool(value))
                continue
            if field == "vset":
                self.apply_vset(slot, channel, float(value))
                continue
            param = self._FIELD_TO_PARAM.get(field)
            if param is None:
                continue
            self.set_channel_param(slot, channel, param, value)
        return dict(values)

    def transition_to_buffered(
        self,
        device_name: str,
        h5file: str,
        initial_values: dict[str, Any],
        fresh: bool,
    ) -> dict[str, Any]:
        _ = (initial_values, fresh)
        with h5py.File(h5file, "r") as f:
            data = f[f"devices/{device_name}/output"]
            if len(data) == 0:
                return {}
            row = data[0]
            values = {name: row[name].item() for name in data.dtype.names}
        return self.program_manual(values, force=True)

    def transition_to_manual(self, abort: bool = False) -> bool:
        _ = abort
        return True

    def abort_buffered(self) -> bool:
        return True

    def abort_transition_to_buffered(self) -> bool:
        return True

    def _parse_connection(self, connection: str) -> tuple[int, int, str] | None:
        m = self._CONN_RE.match(str(connection))
        if not m:
            return None
        return (
            int(m.group("slot")),
            int(m.group("ch")),
            str(m.group("field")).lower(),
        )
