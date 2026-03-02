from __future__ import annotations

import importlib
import math
import os
import sys
from collections import deque
from pathlib import Path
from types import ModuleType
from typing import Any


class ClientWorker:
    """Plain Python worker/service for CAEN devman client operations.

    This class intentionally avoids Qt dependencies so it can be reused in
    BLACS worker contexts and in standalone GUI controllers.
    """

    def __init__(
        self,
        bridge_module: str = "caenhv_devman_bridge",
        bridge_search_paths: list[Path] | None = None,
    ) -> None:
        self._bridge_module_name = bridge_module
        self._bridge_search_paths = list(bridge_search_paths or [])
        self._bridge: ModuleType | None = None
        self._connected = False
        self._client_name: str = ""
        self._owned_resources: set[str] = set()
        self._resource_rows_cache: list[dict[str, Any]] = []
        self._slot_channel_counts: dict[int, int] = {}
        self._slot_negative_polarity: dict[int, bool] = {}
        self._link_rules: dict[tuple[int, int], tuple[tuple[int, int], float]] = {}
        self._channel_state: dict[tuple[int, int], dict[str, Any]] = {}

    def _slot_is_negative(self, slot: int) -> bool:
        return bool(self._slot_negative_polarity.get(int(slot), False))

    def _to_ui_voltage(self, slot: int, param_name: str, value: Any) -> Any:
        name = str(param_name).strip().upper()
        if name not in ("V0SET", "SVMAX"):
            return value
        try:
            num = float(value)
        except Exception:
            return value
        if self._slot_is_negative(slot):
            return -abs(num)
        return num

    def _to_backend_voltage(self, slot: int, param_name: str, value: Any) -> Any:
        name = str(param_name).strip().upper()
        if name not in ("V0SET", "SVMAX"):
            return value
        try:
            num = float(value)
        except Exception:
            return value
        if self._slot_is_negative(slot):
            return abs(num)
        return num

    def _get_param_prop(self, slot: int, channel: int, param_name: str) -> tuple[float, float] | None:
        bridge = self._ensure_bridge()
        if not hasattr(bridge, "Device_get_ch_param_prop"):
            return None
        try:
            prop = bridge.Device_get_ch_param_prop(slot, channel, param_name)
        except Exception:
            return None

        if isinstance(prop, dict):
            minval = prop.get("minval")
            maxval = prop.get("maxval")
        else:
            minval = getattr(prop, "minval", None)
            maxval = getattr(prop, "maxval", None)
        try:
            lo = float(minval)
            hi = float(maxval)
        except Exception:
            return None
        return (lo, hi) if lo <= hi else (hi, lo)

    def fetch_channel_constraints(self, slot: int, channel: int) -> dict[str, float]:
        result: dict[str, float] = {}
        for param_name, out_prefix in (("V0Set", "vset"), ("SVMax", "svmax")):
            prop = self._get_param_prop(slot, channel, param_name)
            if prop is None:
                continue
            lo, hi = prop
            ui_lo = float(self._to_ui_voltage(slot, param_name, lo))
            ui_hi = float(self._to_ui_voltage(slot, param_name, hi))
            if ui_lo > ui_hi:
                ui_lo, ui_hi = ui_hi, ui_lo
            result[f"{out_prefix}_min"] = ui_lo
            result[f"{out_prefix}_max"] = ui_hi
        return result

    def _ensure_bridge(self, *, client_name: str | None = None) -> ModuleType:
        if self._bridge is not None:
            return self._bridge

        for path in self._bridge_search_paths:
            raw = str(path)
            if raw not in sys.path:
                sys.path.insert(0, raw)

        # Generated bridge validates DEVMAN_CLIENT during module import.
        if client_name and not str(os.getenv("DEVMAN_CLIENT", "")).strip():
            os.environ["DEVMAN_CLIENT"] = str(client_name).strip()

        self._bridge = importlib.import_module(self._bridge_module_name)
        return self._bridge

    def connect_client(self, server_host: str, server_port: int, client_name: str, force: bool = False) -> dict[str, Any]:
        bridge = self._ensure_bridge(client_name=client_name)
        bridge.configure_connection(server_host, int(server_port), client_name)
        bridge.connect(force=bool(force))
        self._connected = True
        self._client_name = str(client_name)
        return {
            "server_host": server_host,
            "server_port": int(server_port),
            "client_name": client_name,
            "force": bool(force),
        }

    def disconnect_client(self) -> None:
        bridge = self._ensure_bridge()
        bridge.disconnect()
        self._connected = False
        self._owned_resources.clear()
        self._resource_rows_cache = []
        self._slot_channel_counts = {}
        self._slot_negative_polarity = {}
        self._link_rules = {}
        self._channel_state = {}

    def _board_name(self, board: Any) -> str:
        if isinstance(board, dict):
            for key in ("model", "name", "model_name", "description", "type"):
                value = board.get(key)
                if value is not None and str(value).strip():
                    return str(value)
            return "Board"

        return str(
            getattr(board, "model", None)
            or getattr(board, "name", None)
            or getattr(board, "model_name", None)
            or board.__class__.__name__
        )

    def _board_channels(self, board: Any) -> int:
        if isinstance(board, dict):
            for key in ("n_channel", "n_channels", "num_channels"):
                if key in board and board.get(key) is not None:
                    try:
                        return int(board.get(key))
                    except Exception:
                        return 0
            channels = board.get("channels")
            if isinstance(channels, list):
                return len(channels)
            return 0

        try:
            return int(getattr(board, "n_channel", 0) or 0)
        except Exception:
            return 0

    def _query_owners(self, resources: list[str]) -> dict[str, str | None]:
        if not resources:
            return {}

        bridge = self._ensure_bridge()

        try:
            if hasattr(bridge, "owners_of"):
                raw = bridge.owners_of(resources)
                if isinstance(raw, dict):
                    return {str(k): (None if v is None else str(v)) for k, v in raw.items()}
        except Exception:
            pass

        try:
            if hasattr(bridge, "owner_of"):
                return {resource: bridge.owner_of(resource) for resource in resources}
        except Exception:
            pass

        client = getattr(bridge, "_CLIENT", None)
        if client is not None:
            try:
                if hasattr(client, "owners_of"):
                    raw = client.owners_of(resources)
                    if isinstance(raw, dict):
                        return {str(k): (None if v is None else str(v)) for k, v in raw.items()}
            except Exception:
                pass

            try:
                if hasattr(client, "owner_of"):
                    return {resource: client.owner_of(resource) for resource in resources}
            except Exception:
                pass

        # Last-resort local fallback if ownership query API is unavailable.
        owners: dict[str, str | None] = {}
        for resource in resources:
            owners[resource] = self._client_name if resource in self._owned_resources else None
        return owners

    def _query_channel_names(self, slot: int, channel_count: int) -> dict[int, str]:
        if channel_count <= 0:
            return {}
        bridge = self._ensure_bridge()
        if not hasattr(bridge, "Device_get_ch_name"):
            return {}
        try:
            channel_list = list(range(channel_count))
            raw_names = bridge.Device_get_ch_name(slot, channel_list)
            if not isinstance(raw_names, list):
                return {}
            names: dict[int, str] = {}
            for idx, raw in enumerate(raw_names):
                if idx >= channel_count:
                    break
                names[idx] = str(raw).strip()
            if len(names) != channel_count:
                return {}
            return names
        except Exception:
            return {}

    def _detect_slot_negative_polarity(self, slot: int, channel_count: int, board_name: str) -> bool:
        model = str(board_name).strip().upper()
        # Common CAEN naming uses "...DN" for negative supply variants.
        if model.endswith("DN") or (" DN" in model):
            return True
        if channel_count <= 0:
            return False
        bridge = self._ensure_bridge()
        if not hasattr(bridge, "Device_get_ch_param_prop"):
            return False
        channel = 0
        try:
            prop = bridge.Device_get_ch_param_prop(slot, channel, "V0Set")
        except Exception:
            return False

        if isinstance(prop, dict):
            minval = prop.get("minval")
            maxval = prop.get("maxval")
        else:
            minval = getattr(prop, "minval", None)
            maxval = getattr(prop, "maxval", None)

        try:
            minf = float(minval) if minval is not None else None
            maxf = float(maxval) if maxval is not None else None
        except Exception:
            return False
        if minf is None or maxf is None:
            return False

        # Fixed negative polarity boards typically expose non-positive V0Set range.
        if maxf <= 0.0 and minf < 0.0:
            return True
        return False

    def _build_resource_topology(self) -> list[dict[str, Any]]:
        bridge = self._ensure_bridge()
        if not hasattr(bridge, "Device_get_crate_map"):
            return []

        rows: list[dict[str, Any]] = []
        self._slot_channel_counts = {}
        self._slot_negative_polarity = {}
        crate_map = bridge.Device_get_crate_map()
        for slot, board in enumerate(crate_map):
            if board is None:
                continue

            board_name = self._board_name(board)
            channel_count = self._board_channels(board)
            self._slot_channel_counts[slot] = channel_count
            negative_polarity = self._detect_slot_negative_polarity(slot, channel_count, board_name)
            self._slot_negative_polarity[slot] = bool(negative_polarity)
            rows.append(
                {
                    "row_type": "slot",
                    "slot": slot,
                    "board": board_name,
                    "channel": None,
                    "owner": "",
                    "action": "",
                    "resource": f"slot:{slot}",
                    "negative_polarity": bool(negative_polarity),
                }
            )

            channel_names = self._query_channel_names(slot, channel_count)
            for ch in range(channel_count):
                rows.append(
                    {
                        "row_type": "channel",
                        "slot": slot,
                        "board": board_name,
                        "channel": ch,
                        "channel_label": f"{ch}: {channel_names[ch]}" if ch in channel_names else str(ch),
                        "owner": "",
                        "action": "",
                        "resource": f"slot:{slot}:ch:{ch}",
                        "negative_polarity": bool(negative_polarity),
                    }
                )
        return rows

    def _is_linked_source(self, slot: int, channel: int) -> bool:
        return (int(slot), int(channel)) in self._link_rules

    def set_link_rule(
        self,
        slot: int,
        channel: int,
        reference: tuple[int, int] | None,
        offset: float = 0.0,
        *,
        sync_ramps: bool = False,
    ) -> dict[str, float] | None:
        key = (int(slot), int(channel))
        if reference is None:
            self._link_rules.pop(key, None)
            return None
        ref_slot, ref_channel = int(reference[0]), int(reference[1])
        if key == (ref_slot, ref_channel):
            raise RuntimeError("reference channel cannot be itself")
        self._link_rules[key] = ((ref_slot, ref_channel), float(offset))
        if sync_ramps:
            return self._sync_link_ramps(key, (ref_slot, ref_channel))
        return None

    def set_link_offset(self, slot: int, channel: int, offset: float) -> None:
        key = (int(slot), int(channel))
        current = self._link_rules.get(key)
        if current is None:
            raise RuntimeError(f"channel {slot}:{channel} has no reference link")
        self._link_rules[key] = (current[0], float(offset))

    def get_link_offset(self, slot: int, channel: int) -> float | None:
        key = (int(slot), int(channel))
        current = self._link_rules.get(key)
        if current is None:
            return None
        return float(current[1])

    def get_link_reference(self, slot: int, channel: int) -> tuple[int, int] | None:
        key = (int(slot), int(channel))
        current = self._link_rules.get(key)
        if current is None:
            return None
        reference, _offset = current
        return int(reference[0]), int(reference[1])

    def drop_stale_links(self, active_channels: set[tuple[int, int]]) -> None:
        active = {(int(s), int(c)) for (s, c) in active_channels}
        self._link_rules = {k: v for k, v in self._link_rules.items() if k in active and v[0] in active}

    def _to_bool(self, value: Any) -> bool:
        if isinstance(value, bool):
            return value
        try:
            return bool(int(value))
        except Exception:
            return bool(value)

    def _get_numeric_param(self, slot: int, channel: int, param: str) -> float | None:
        bridge = self._ensure_bridge()
        try:
            values = bridge.Device_get_ch_param(int(slot), [int(channel)], str(param))
            if not isinstance(values, list) or not values:
                return None
            return float(values[0])
        except Exception:
            return None

    def _get_numeric_param_any(self, slot: int, channel: int, names: list[str]) -> tuple[float, str] | None:
        for name in names:
            value = self._get_numeric_param(slot, channel, name)
            if value is not None:
                return float(value), str(name)
        return None

    def _set_param_any(self, slot: int, channel: int, names: list[str], value: float) -> str | None:
        for name in names:
            try:
                self.set_channel_param(int(slot), int(channel), str(name), float(value))
                return str(name)
            except Exception:
                continue
        return None

    def _sync_link_ramps(self, ch_a: tuple[int, int], ch_b: tuple[int, int]) -> dict[str, float]:
        a_slot, a_ch = int(ch_a[0]), int(ch_a[1])
        b_slot, b_ch = int(ch_b[0]), int(ch_b[1])

        a_rup = self._get_numeric_param_any(a_slot, a_ch, ["RUp", "RUP"])
        b_rup = self._get_numeric_param_any(b_slot, b_ch, ["RUp", "RUP"])
        a_rdown = self._get_numeric_param_any(a_slot, a_ch, ["RDWn", "RDown", "RDWN"])
        b_rdown = self._get_numeric_param_any(b_slot, b_ch, ["RDWn", "RDown", "RDWN"])

        target_rup: float | None = None
        target_rdown: float | None = None
        if a_rup is not None and b_rup is not None:
            target_rup = min(float(a_rup[0]), float(b_rup[0]))
        if a_rdown is not None and b_rdown is not None:
            target_rdown = min(float(a_rdown[0]), float(b_rdown[0]))

        if target_rup is not None:
            self._set_param_any(a_slot, a_ch, ["RUp", "RUP"], float(target_rup))
            self._set_param_any(b_slot, b_ch, ["RUp", "RUP"], float(target_rup))
        if target_rdown is not None:
            self._set_param_any(a_slot, a_ch, ["RDWn", "RDown", "RDWN"], float(target_rdown))
            self._set_param_any(b_slot, b_ch, ["RDWn", "RDown", "RDWN"], float(target_rdown))

        result: dict[str, float] = {}
        if target_rup is not None:
            result["rup"] = float(target_rup)
        if target_rdown is not None:
            result["rdown"] = float(target_rdown)
        return result

    def _get_channel_state(self, slot: int, channel: int) -> dict[str, Any]:
        key = (int(slot), int(channel))
        cached = self._channel_state.get(key)
        if cached is not None and "vset" in cached and "power" in cached:
            return dict(cached)
        payload = self.fetch_channel_settings(int(slot), int(channel))
        state = {
            "vset": float(payload.get("vset", 0.0)),
            "power": self._to_bool(payload.get("power", False)),
        }
        self._channel_state[key] = dict(state)
        return state

    def update_cached_channel_settings(self, slot: int, channel: int, payload: dict[str, Any]) -> None:
        key = (int(slot), int(channel))
        state = dict(self._channel_state.get(key) or {})
        if "vset" in payload:
            state["vset"] = float(payload["vset"])
        if "power" in payload:
            state["power"] = self._to_bool(payload["power"])
        if state:
            self._channel_state[key] = state

    def _children_of_reference(self) -> dict[tuple[int, int], list[tuple[int, int]]]:
        children: dict[tuple[int, int], list[tuple[int, int]]] = {}
        for source, (reference, _offset) in self._link_rules.items():
            children.setdefault(reference, []).append(source)
        return children

    def _linked_neighbors(self) -> dict[tuple[int, int], set[tuple[int, int]]]:
        neighbors: dict[tuple[int, int], set[tuple[int, int]]] = {}
        for source, (reference, _offset) in self._link_rules.items():
            neighbors.setdefault(source, set()).add(reference)
            neighbors.setdefault(reference, set()).add(source)
        return neighbors

    def get_linked_channels_recursive(self, slot: int, channel: int) -> set[tuple[int, int]]:
        start = (int(slot), int(channel))
        neighbors = self._linked_neighbors()
        if start not in neighbors:
            return {start}
        visited: set[tuple[int, int]] = set()
        queue: deque[tuple[int, int]] = deque([start])
        while queue:
            node = queue.popleft()
            if node in visited:
                continue
            visited.add(node)
            for nxt in neighbors.get(node, set()):
                if nxt not in visited:
                    queue.append(nxt)
        return visited or {start}

    def _build_linked_targets(
        self,
        *,
        requested_values: dict[tuple[int, int], float],
    ) -> dict[tuple[int, int], float]:
        targets = {k: float(v) for k, v in requested_values.items()}
        children = self._children_of_reference()
        work = deque(targets.keys())
        while work:
            reference = work.popleft()
            reference_v = targets.get(reference)
            if reference_v is None:
                continue
            for source in children.get(reference, []):
                _, offset = self._link_rules[source]
                source_v = float(reference_v) + float(offset)
                old = targets.get(source)
                if old is None:
                    targets[source] = source_v
                    work.append(source)
                    continue
                if not math.isclose(float(old), source_v, rel_tol=0.0, abs_tol=1e-6):
                    raise RuntimeError(
                        f"conflicting linked targets for channel {source[0]}:{source[1]}"
                    )
        return targets

    def _validate_linked_power_consistency(
        self,
        *,
        initiator: tuple[int, int],
        affected: set[tuple[int, int]],
    ) -> None:
        init_state = self._get_channel_state(initiator[0], initiator[1])
        init_on = self._to_bool(init_state.get("power", False))
        for slot, channel in sorted(affected):
            state = self._get_channel_state(slot, channel)
            ch_on = self._to_bool(state.get("power", False))
            if init_on and not ch_on:
                raise RuntimeError(
                    f"linked queue rejected: initiator {initiator[0]}:{initiator[1]} is ON "
                    f"but linked channel {slot}:{channel} is OFF"
                )
            if (not init_on) and ch_on:
                raise RuntimeError(
                    f"linked queue rejected: initiator {initiator[0]}:{initiator[1]} is OFF "
                    f"but linked channel {slot}:{channel} is ON"
                )

    def _set_channel_power(self, slot: int, channel: int, enabled: bool) -> None:
        self.set_channel_param(int(slot), int(channel), "Pw", 1 if bool(enabled) else 0)
        key = (int(slot), int(channel))
        state = self._get_channel_state(int(slot), int(channel))
        state["power"] = bool(enabled)
        self._channel_state[key] = state

    def set_power_for_channels(self, channels: set[tuple[int, int]], enabled: bool) -> None:
        for slot, channel in sorted({(int(s), int(c)) for (s, c) in channels}):
            self._set_channel_power(slot, channel, bool(enabled))

    def _execute_vset_plan(self, targets: dict[tuple[int, int], float]) -> None:
        states: dict[tuple[int, int], dict[str, Any]] = {
            key: self._get_channel_state(key[0], key[1]) for key in targets
        }
        pre_vsets: dict[tuple[int, int], float] = {
            key: float(state.get("vset", 0.0)) for key, state in states.items()
        }

        # Build precedence constraints per linked pair from requested shift
        # direction and offset sign:
        # - positive shift  => negative-offset channel first
        # - negative shift  => positive-offset channel first
        # This is equivalent to: source-first iff shift * offset < 0.
        edges: dict[tuple[int, int], set[tuple[int, int]]] = {k: set() for k in targets}
        indeg: dict[tuple[int, int], int] = {k: 0 for k in targets}

        for source, (reference, offset) in self._link_rules.items():
            if source not in targets or reference not in targets:
                continue

            src_now = float(states[source].get("vset", 0.0))
            src_target = float(targets[source])
            shift = float(src_target - src_now)
            off = float(offset)

            before: tuple[int, int]
            after: tuple[int, int]
            if abs(shift) <= 1e-12 or abs(off) <= 1e-12:
                # Zero-shift / zero-offset link: keep stable reference-first.
                before, after = reference, source
            elif shift * off < 0.0:
                before, after = source, reference
            else:
                before, after = reference, source

            if after not in edges[before]:
                edges[before].add(after)
                indeg[after] += 1

        queue: list[tuple[tuple[int, int], float]] = []
        ready = sorted([k for k, deg in indeg.items() if deg == 0])
        while ready:
            node = ready.pop(0)
            queue.append((node, float(targets[node])))
            for nxt in sorted(edges[node]):
                indeg[nxt] -= 1
                if indeg[nxt] == 0:
                    ready.append(nxt)
            ready.sort()

        if len(queue) != len(targets):
            raise RuntimeError("linked channels contain a cycle or conflicting order constraints")

        queued: set[tuple[int, int]] = set()
        executed: list[tuple[int, int]] = []
        for (slot, channel), target_v in queue:
            key = (int(slot), int(channel))
            if key in queued:
                raise RuntimeError(f"duplicate queued modification for {slot}:{channel}")
            queued.add(key)
            state = self._get_channel_state(slot, channel)
            self.set_channel_param(slot, channel, "V0Set", float(target_v))
            state["vset"] = float(target_v)
            self._channel_state[key] = state
            executed.append(key)

        # Refresh actual set values from backend after queue execution.
        bridge = self._ensure_bridge()
        by_slot: dict[int, list[int]] = {}
        for slot, channel in executed:
            by_slot.setdefault(int(slot), []).append(int(channel))
        for slot, channels in by_slot.items():
            unique_channels = sorted(set(channels))
            try:
                values = bridge.Device_get_ch_param(int(slot), unique_channels, "V0Set")
            except Exception:
                continue
            if not isinstance(values, list):
                continue
            for idx, ch in enumerate(unique_channels):
                if idx >= len(values):
                    break
                key = (int(slot), int(ch))
                state = dict(self._channel_state.get(key) or {})
                queried = float(self._to_ui_voltage(slot, "V0Set", values[idx]))
                target = float(targets.get(key, queried))
                prev = float(pre_vsets.get(key, queried))
                # If backend echoes previous value right after set (stale read),
                # keep command target instead of reverting widget/cache.
                if math.isclose(queried, prev, rel_tol=0.0, abs_tol=1e-6) and not math.isclose(
                    target, prev, rel_tol=0.0, abs_tol=1e-6
                ):
                    state["vset"] = target
                else:
                    state["vset"] = queried
                self._channel_state[key] = state

    def get_cached_channel_settings(self, slot: int, channel: int) -> dict[str, Any]:
        key = (int(slot), int(channel))
        cached = dict(self._channel_state.get(key) or {})
        if "vset" in cached or "power" in cached:
            return cached
        payload = self.fetch_channel_settings(int(slot), int(channel))
        result: dict[str, Any] = {}
        if "vset" in payload:
            result["vset"] = float(payload["vset"])
        if "power" in payload:
            result["power"] = self._to_bool(payload["power"])
        return result

    def _clone_rows(self, rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return [dict(row) for row in rows]

    def _apply_dynamic_state(self, rows: list[dict[str, Any]], *, refresh_names: bool) -> list[dict[str, Any]]:
        if refresh_names:
            for slot, channel_count in self._slot_channel_counts.items():
                names = self._query_channel_names(slot, channel_count)
                if len(names) != channel_count:
                    # Keep existing labels to avoid transient blank/flicker on partial/failed name fetches.
                    continue
                for row in rows:
                    if int(row.get("slot", -1)) != slot:
                        continue
                    channel = row.get("channel")
                    if channel is None:
                        continue
                    ch = int(channel)
                    name_text = str(names.get(ch, "")).strip()
                    row["channel_label"] = f"{ch}: {name_text}" if name_text else str(ch)
                    row["negative_polarity"] = bool(self._slot_negative_polarity.get(slot, False))

        channel_resources = [
            str(row.get("resource"))
            for row in rows
            if row.get("channel") is not None and row.get("resource") is not None
        ]
        owners = self._query_owners(channel_resources)
        for row in rows:
            if row.get("channel") is None:
                continue
            resource = str(row.get("resource") or "")
            owner = owners.get(resource)
            owner_text = "" if owner is None else str(owner)
            row["owner"] = owner_text
            if not owner_text:
                row["action"] = "acquire"
            elif owner_text == self._client_name:
                row["action"] = "release"
            else:
                row["action"] = ""
        return rows

    def refresh_resources(self) -> list[dict[str, Any]]:
        self._resource_rows_cache = self._build_resource_topology()
        return self._apply_dynamic_state(self._clone_rows(self._resource_rows_cache), refresh_names=False)

    def refresh_resources_cached(self) -> list[dict[str, Any]]:
        if not self._resource_rows_cache:
            return self.refresh_resources()
        return self._apply_dynamic_state(self._clone_rows(self._resource_rows_cache), refresh_names=True)

    def refresh_channel_snapshot(self, slot: int, channel: int) -> dict[str, Any]:
        bridge = self._ensure_bridge()
        payload: dict[str, Any] = {}
        # These parameters are standard on many CAEN systems; tolerate failures.
        try:
            payload["vmon"] = bridge.Device_get_ch_param(slot, [channel], "VMon")[0]
        except Exception:
            pass
        try:
            payload["imon"] = bridge.Device_get_ch_param(slot, [channel], "IMon")[0]
        except Exception:
            pass
        try:
            payload["status"] = bridge.Device_get_ch_param(slot, [channel], "Status")[0]
        except Exception:
            pass
        return payload

    def fetch_channel_settings(self, slot: int, channel: int) -> dict[str, Any]:
        bridge = self._ensure_bridge()
        payload: dict[str, Any] = {}
        try:
            names = bridge.Device_get_ch_name(slot, [channel])
            if isinstance(names, list) and names:
                payload["label"] = names[0]
        except Exception:
            pass

        params = (
            ("V0Set", "vset"),
            ("Pw", "power"),
            ("RUp", "rup"),
            ("Trip", "trip"),
            ("SVMax", "svmax"),
            ("PDWN", "pdown"),
        )
        for param_name, key in params:
            try:
                value = bridge.Device_get_ch_param(slot, [channel], param_name)[0]
                if param_name in ("V0Set", "SVMax"):
                    value = self._to_ui_voltage(slot, param_name, value)
                payload[key] = value
            except Exception:
                pass
        if "iset" not in payload:
            for iset_name in ("I0Set", "ISet", "ISET"):
                try:
                    payload["iset"] = bridge.Device_get_ch_param(slot, [channel], iset_name)[0]
                    break
                except Exception:
                    continue
        if "rdown" not in payload:
            for rdown_name in ("RDWn", "RDown", "RDWN"):
                try:
                    payload["rdown"] = bridge.Device_get_ch_param(slot, [channel], rdown_name)[0]
                    break
                except Exception:
                    continue
        self.update_cached_channel_settings(slot, channel, payload)
        return payload

    def set_channel_param(self, slot: int, channel: int, name: str, value: Any) -> None:
        bridge = self._ensure_bridge()
        write_value = self._to_backend_voltage(slot, name, value)
        bridge.Device_set_ch_param(slot, [channel], name, write_value)

    def set_param_for_channels(
        self,
        channels: set[tuple[int, int]],
        name: str,
        value: Any,
    ) -> None:
        for slot, channel in sorted({(int(s), int(c)) for (s, c) in channels}):
            self.set_channel_param(int(slot), int(channel), str(name), value)

    def set_channel_name(self, slot: int, channel: int, label: str) -> None:
        bridge = self._ensure_bridge()
        bridge.Device_set_ch_name(slot, [channel], str(label))

    def acquire_resource(self, resource: str) -> bool:
        bridge = self._ensure_bridge()
        acquired = bool(bridge.acquire(resource))
        if acquired:
            self._owned_resources.add(str(resource))
        return acquired

    def release_resource(self, resource: str) -> bool:
        bridge = self._ensure_bridge()
        released = bool(bridge.release(resource))
        if released:
            self._owned_resources.discard(str(resource))
        return released

    def apply_linked_vset(self, slot: int, channel: int, requested_vset: float) -> None:
        key = (int(slot), int(channel))
        current_rule = self._link_rules.get(key)
        if current_rule is not None:
            reference, _offset = current_rule
            ref_state = self._get_channel_state(reference[0], reference[1])
            ref_vset = float(ref_state.get("vset", 0.0))
            self._link_rules[key] = (reference, float(requested_vset) - ref_vset)
        targets = self._build_linked_targets(requested_values={key: float(requested_vset)})
        self._validate_linked_power_consistency(initiator=key, affected=set(targets.keys()))
        self._execute_vset_plan(targets)

    def apply_linked_offset(self, slot: int, channel: int, offset: float) -> None:
        key = (int(slot), int(channel))
        current = self._link_rules.get(key)
        if current is None:
            raise RuntimeError(f"channel {slot}:{channel} has no reference link")
        reference, _ = current
        self._link_rules[key] = (reference, float(offset))
        ref_state = self._get_channel_state(reference[0], reference[1])
        target_vset = float(ref_state.get("vset", 0.0)) + float(offset)
        targets = self._build_linked_targets(requested_values={key: target_vset})
        self._validate_linked_power_consistency(initiator=key, affected=set(targets.keys()))
        self._execute_vset_plan(targets)

    def apply_linked_power(self, slot: int, channel: int, enabled: bool) -> None:
        key = (int(slot), int(channel))
        self._set_channel_power(key[0], key[1], bool(enabled))
