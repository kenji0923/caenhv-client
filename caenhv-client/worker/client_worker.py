from __future__ import annotations

import importlib
import math
import os
import re
import sys
from collections import deque
from pathlib import Path
from types import ModuleType
from typing import Any


class ChannelError(RuntimeError):
    """A worker error attributable to one channel.

    Carries a machine-addressable ``channel`` ("slot:ch") so a remote caller
    can act on the offending channel instead of parsing it out of the message.
    """

    def __init__(self, slot: int, channel: int, message: str) -> None:
        self.slot = int(slot)
        self.ch = int(channel)
        self.channel = f"{self.slot}:{self.ch}"
        super().__init__(message)


class ClientWorker:
    """Plain Python worker/service for CAEN devman client operations.

    This class intentionally avoids Qt dependencies so it can be reused in
    BLACS worker contexts and in standalone GUI controllers.
    """

    def __init__(
        self,
        bridge_module: str = "caenhv_devman_client.client",
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
        self.link_push_status: str = ""
        self.trip_line_status: str = ""
        self._param_names_cache: dict[tuple[int, int], set[str]] = {}
        self._internal_trip_lines_cache: dict[int, int] = {}
        self._trip_line_alloc: dict[frozenset[tuple[int, int]], tuple[str, int]] = {}
        self._trip_lines_scanned = False
        self._external_trip_lines_in_use: set[int] = set()
        self._internal_trip_lines_in_use: dict[int, set[int]] = {}

    def _slot_is_negative(self, slot: int) -> bool:
        return bool(self._slot_negative_polarity.get(int(slot), False))

    def _to_ui_voltage(self, slot: int, param_name: str, value: Any) -> Any:
        name = str(param_name).strip().upper()
        if name in ("V0SET", "SVMAX", "VMON"):
            try:
                num = float(value)
            except Exception:
                return value
            return -abs(num) if self._slot_is_negative(slot) else num
        if name in ("RUP", "RDWN", "RDOWN"):
            # Signed slew convention: the sign is the direction of signed-
            # voltage motion the parameter governs (matches Telegraf logging).
            try:
                num = float(value)
            except Exception:
                return value
            negative = self._slot_is_negative(slot)
            if name == "RUP":
                return -abs(num) if negative else abs(num)
            return abs(num) if negative else -abs(num)
        return value

    def _to_backend_voltage(self, slot: int, param_name: str, value: Any) -> Any:
        name = str(param_name).strip().upper()
        if name in ("V0SET", "SVMAX"):
            try:
                num = float(value)
            except Exception:
                return value
            return abs(num) if self._slot_is_negative(slot) else num
        if name in ("RUP", "RDWN", "RDOWN"):
            # CAEN always takes ramp rates as magnitudes.
            try:
                num = float(value)
            except Exception:
                return value
            return abs(num)
        return value

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
        for names, out_key in ((["RUp", "RUP"], "rup_max"), (["RDWn", "RDown", "RDWN"], "rdwn_max")):
            for param_name in names:
                prop = self._get_param_prop(slot, channel, param_name)
                if prop is not None:
                    result[out_key] = max(abs(float(prop[0])), abs(float(prop[1])))
                    break
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
        self._param_names_cache = {}
        self._internal_trip_lines_cache = {}
        self._trip_lines_scanned = False
        self._external_trip_lines_in_use = set()
        self._internal_trip_lines_in_use = {}

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
    ) -> dict[str, Any] | None:
        key = (int(slot), int(channel))
        if reference is None:
            removed = self._link_rules.pop(key, None)
            if removed is not None:
                self._after_link_change()
            return None
        ref_slot, ref_channel = int(reference[0]), int(reference[1])
        if key == (ref_slot, ref_channel):
            raise RuntimeError("reference channel cannot be itself")
        previous_rule = self._link_rules.get(key)
        self._link_rules[key] = ((ref_slot, ref_channel), float(offset))
        if not sync_ramps:
            self._after_link_change()
            return None
        # The link is only kept if the group could actually be synchronized;
        # a partial sync would leave linked channels with unequal rates.
        try:
            group = self.get_linked_channels_recursive(key[0], key[1])
            updates: dict[str, Any] = dict(self._sync_link_ramps(group))
            pdown = self._sync_link_pdown(group, adopt_from=(ref_slot, ref_channel))
            if pdown is not None:
                updates["pdown"] = pdown
        except Exception:
            if previous_rule is None:
                self._link_rules.pop(key, None)
            else:
                self._link_rules[key] = previous_rule
            raise
        self._after_link_change()
        return updates

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

    def link_relationships(self) -> dict[str, dict[str, Any]]:
        """All active links as {source 'slot:ch': {reference, offset}}."""
        return {
            f"{s[0]}:{s[1]}": {"reference": f"{r[0]}:{r[1]}", "offset": float(o)}
            for s, (r, o) in self._link_rules.items()
        }

    def link_info(self, slot: int, channel: int) -> dict[str, Any]:
        """Link relationship of one channel (remote-API shape)."""
        reference = self.get_link_reference(int(slot), int(channel))
        offset = self.get_link_offset(int(slot), int(channel))
        return {
            "linked": reference is not None,
            "master_slot": (reference[0] if reference else None),
            "master_channel": (reference[1] if reference else None),
            "offset": (float(offset) if offset is not None else 0.0),
        }

    def read_channel_brief(self, slot: int, channel: int) -> dict[str, Any]:
        """Essential monitoring fields for bulk reads (same names/signs as get).

        A failed vset/power sub-read leaves its key absent and records the
        reason under ``errors`` ({"vset": "<msg>"}), so a partial failure is
        visible to the caller instead of silently missing. A failed core
        snapshot (vmon/status) still raises, so the caller marks the whole
        channel {"error": ...}.
        """
        bridge = self._ensure_bridge()
        payload = self.refresh_channel_snapshot(int(slot), int(channel))  # vmon, imon, status
        errors: dict[str, str] = {}
        try:
            raw = bridge.Device_get_ch_param(int(slot), [int(channel)], "V0Set")[0]
            payload["vset"] = self._to_ui_voltage(int(slot), "V0Set", raw)
        except Exception as exc:
            errors["vset"] = str(exc)
        last_exc: Exception | None = None
        for pw_name in ("Pw", "PW", "Pon"):
            try:
                payload["power"] = bridge.Device_get_ch_param(int(slot), [int(channel)], pw_name)[0]
                break
            except Exception as exc:
                last_exc = exc
                continue
        else:
            errors["power"] = str(last_exc) if last_exc is not None else "power not readable"
        if errors:
            payload["errors"] = errors
        if not payload:
            raise RuntimeError(f"channel {slot}:{channel} could not be read")
        return payload

    def get_link_reference(self, slot: int, channel: int) -> tuple[int, int] | None:
        key = (int(slot), int(channel))
        current = self._link_rules.get(key)
        if current is None:
            return None
        reference, _offset = current
        return int(reference[0]), int(reference[1])

    def drop_stale_links(self, active_channels: set[tuple[int, int]]) -> None:
        active = {(int(s), int(c)) for (s, c) in active_channels}
        before = len(self._link_rules)
        self._link_rules = {k: v for k, v in self._link_rules.items() if k in active and v[0] in active}
        if len(self._link_rules) != before:
            self._after_link_change()

    @staticmethod
    def _channel_resource(slot: int, channel: int) -> str:
        return f"slot:{int(slot)}:ch:{int(channel)}"

    def _link_group_components(self) -> list[set[tuple[int, int]]]:
        neighbors = self._linked_neighbors()
        seen: set[tuple[int, int]] = set()
        components: list[set[tuple[int, int]]] = []
        for start in sorted(neighbors):
            if start in seen:
                continue
            component: set[tuple[int, int]] = set()
            queue: deque[tuple[int, int]] = deque([start])
            while queue:
                node = queue.popleft()
                if node in component:
                    continue
                component.add(node)
                queue.extend(neighbors.get(node, set()) - component)
            seen |= component
            if len(component) >= 2:
                components.append(component)
        return components

    def link_groups(self) -> list[list[str]]:
        """Connected components of the link graph as resource-string groups."""
        return [
            [self._channel_resource(s, c) for s, c in sorted(component)]
            for component in self._link_group_components()
        ]

    def push_link_groups(self) -> None:
        """Best-effort sync of the link groups to the devman server registry.

        The server-side trip watchdog protects registered groups even while
        this client is closed. Failures are recorded in link_push_status for
        the UI to report; they never break the local link operation.
        """
        # Never issue a request while disconnected: the bridge auto-reconnects
        # on any call, which would silently re-register this client after an
        # explicit disconnect and block the next connect.
        if not self._connected:
            return
        try:
            bridge = self._ensure_bridge()
            if not hasattr(bridge, "set_link_groups"):
                self.link_push_status = "unsupported"
                return
            count = int(bridge.set_link_groups(self.link_groups()))
            self.link_push_status = f"ok:{count}"
        except Exception as exc:
            self.link_push_status = f"error: {exc}"

    def list_registered_link_groups(self) -> dict[str, list[list[str]]] | None:
        """All link groups registered on the server, or None if unsupported."""
        bridge = self._ensure_bridge()
        if not hasattr(bridge, "list_link_groups"):
            return None
        return bridge.list_link_groups()

    def drop_links_for_resource(self, resource: str) -> list[tuple[int, int]]:
        """Drop link rules involving channels covered by a released resource.

        Returns the source keys whose rules were removed so the UI can clear
        their reference selections.
        """
        text = str(resource).strip()
        match_channel = re.match(r"^slot:(\d+):ch:(\d+)$", text)
        match_slot = re.match(r"^slot:(\d+)$", text)

        def covers(key: tuple[int, int]) -> bool:
            if match_channel:
                return key == (int(match_channel.group(1)), int(match_channel.group(2)))
            if match_slot:
                return key[0] == int(match_slot.group(1))
            return False

        dropped: list[tuple[int, int]] = []
        for source, (reference, _offset) in list(self._link_rules.items()):
            if covers(source) or covers(reference):
                self._link_rules.pop(source, None)
                dropped.append(source)
        if dropped:
            self._after_link_change()
        return sorted(dropped)

    def _after_link_change(self) -> None:
        self.push_link_groups()
        self.sync_trip_lines()

    # --- Hardware trip lines (TripInt / TripExt) -------------------------
    #
    # CAEN boards expose TripInt (per-board internal trip bus, 2N-bit word:
    # bits [0..N-1] sense a line, bits [N..2N-1] propagate onto it) and
    # TripExt (crate-wide external trip bus, 8-bit word: bits 0-3 sense
    # lines 0-3, bits 4-7 propagate). Channels sharing a line trip together
    # in hardware, with no software in the loop.

    _TRIPINT_NAMES = ["TripInt", "TRIPINT"]
    _TRIPEXT_NAMES = ["TripExt", "TRIPEXT"]
    _EXTERNAL_TRIP_LINES = 4

    def _channel_param_names(self, slot: int, channel: int) -> set[str]:
        key = (int(slot), int(channel))
        cached = self._param_names_cache.get(key)
        if cached is not None:
            return cached
        names: set[str] = set()
        try:
            bridge = self._ensure_bridge()
            info = bridge.Device_get_ch_param_info(int(slot), int(channel))
            if isinstance(info, (list, tuple)):
                names = {str(n) for n in info}
        except Exception:
            names = set()
        self._param_names_cache[key] = names
        return names

    def _first_param_name(self, slot: int, channel: int, candidates: list[str]) -> str | None:
        names = self._channel_param_names(slot, channel)
        for name in candidates:
            if name in names:
                return name
        return None

    def _internal_trip_line_count(self, slot: int) -> int:
        cached = self._internal_trip_lines_cache.get(int(slot))
        if cached is not None:
            return cached
        count = 0
        name = self._first_param_name(int(slot), 0, self._TRIPINT_NAMES)
        if name is not None:
            prop = self._get_param_prop(int(slot), 0, name)
            if prop is not None:
                # TripInt is a 2N-bit word, so maxval = 2^(2N) - 1.
                maxval = int(prop[1])
                bits = maxval.bit_length()
                if maxval == (1 << bits) - 1 and bits % 2 == 0:
                    count = bits // 2
        self._internal_trip_lines_cache[int(slot)] = count
        return count

    def _scan_trip_lines_in_use(self) -> None:
        if self._trip_lines_scanned:
            return
        self._trip_lines_scanned = True
        bridge = self._ensure_bridge()
        for slot, channel_count in self._slot_channel_counts.items():
            channels = list(range(int(channel_count)))
            if not channels:
                continue
            ext_name = self._first_param_name(int(slot), 0, self._TRIPEXT_NAMES)
            if ext_name is not None:
                try:
                    values = bridge.Device_get_ch_param(int(slot), channels, ext_name)
                    for value in values or []:
                        word = int(value)
                        for line in range(self._EXTERNAL_TRIP_LINES):
                            if word & ((1 << line) | (1 << (4 + line))):
                                self._external_trip_lines_in_use.add(line)
                except Exception:
                    pass
            line_count = self._internal_trip_line_count(int(slot))
            int_name = self._first_param_name(int(slot), 0, self._TRIPINT_NAMES)
            if int_name is not None and line_count:
                try:
                    values = bridge.Device_get_ch_param(int(slot), channels, int_name)
                    used = self._internal_trip_lines_in_use.setdefault(int(slot), set())
                    for value in values or []:
                        word = int(value)
                        for line in range(line_count):
                            if word & ((1 << line) | (1 << (line_count + line))):
                                used.add(line)
                except Exception:
                    pass

    def _write_group_trip_masks(self, members: list[tuple[int, int]], names: list[str], mask: int) -> bool:
        written: list[tuple[int, int, str]] = []
        for slot, channel in members:
            name = self._first_param_name(slot, channel, names)
            if name is None:
                return False
            try:
                self.set_channel_param(int(slot), int(channel), name, int(mask))
                written.append((slot, channel, name))
            except Exception:
                # A half-programmed line is worse than none: undo what landed.
                for w_slot, w_channel, w_name in written:
                    try:
                        self.set_channel_param(int(w_slot), int(w_channel), w_name, 0)
                    except Exception:
                        pass
                return False
        return True

    def _clear_group_trip_masks(self, group: frozenset[tuple[int, int]], kind: str) -> None:
        names = self._TRIPEXT_NAMES if kind == "ext" else self._TRIPINT_NAMES
        for slot, channel in sorted(group):
            name = self._first_param_name(slot, channel, names)
            if name is None:
                continue
            try:
                self.set_channel_param(int(slot), int(channel), name, 0)
            except Exception:
                pass

    def _program_group_trip_line(self, group: frozenset[tuple[int, int]]) -> str | None:
        members = sorted(group)
        slots = {int(s) for s, _c in members}
        self._scan_trip_lines_in_use()
        # Same-board groups prefer an internal line, saving the 4 crate lines
        # for the groups that need them (mixed polarity spans two boards).
        if len(slots) == 1:
            slot = next(iter(slots))
            line_count = self._internal_trip_line_count(slot)
            if line_count and all(
                self._first_param_name(s, c, self._TRIPINT_NAMES) for s, c in members
            ):
                used = self._internal_trip_lines_in_use.setdefault(slot, set())
                for line in range(line_count):
                    if line in used:
                        continue
                    mask = (1 << line) | (1 << (line_count + line))
                    if self._write_group_trip_masks(members, self._TRIPINT_NAMES, mask):
                        used.add(line)
                        self._trip_line_alloc[group] = ("int", line)
                        return "int"
                    return None
        if not all(self._first_param_name(s, c, self._TRIPEXT_NAMES) for s, c in members):
            return None
        for line in range(self._EXTERNAL_TRIP_LINES):
            if line in self._external_trip_lines_in_use:
                continue
            mask = (1 << line) | (1 << (4 + line))
            if self._write_group_trip_masks(members, self._TRIPEXT_NAMES, mask):
                self._external_trip_lines_in_use.add(line)
                self._trip_line_alloc[group] = ("ext", line)
                return "ext"
            return None
        return None

    def _free_trip_line(self, group: frozenset[tuple[int, int]], kind: str, line: int) -> None:
        if kind == "ext":
            self._external_trip_lines_in_use.discard(int(line))
        else:
            slot = next(iter(group))[0]
            self._internal_trip_lines_in_use.get(int(slot), set()).discard(int(line))

    def sync_trip_lines(self) -> None:
        """Reconcile hardware trip-bus masks with the current link groups.

        Best-effort: capability gaps and failures are recorded in
        trip_line_status; groups without a hardware line are still covered
        by the server watchdog and the GUI trip reaction.
        """
        try:
            components = [frozenset(c) for c in self._link_group_components()]
            desired = set(components)
            for old_group, (kind, line) in list(self._trip_line_alloc.items()):
                if old_group in desired:
                    continue
                self._clear_group_trip_masks(old_group, kind)
                self._free_trip_line(old_group, kind, line)
                self._trip_line_alloc.pop(old_group, None)
            ext_count = 0
            int_count = 0
            uncovered = 0
            for group in components:
                existing = self._trip_line_alloc.get(group)
                if existing is None:
                    existing_kind = self._program_group_trip_line(group)
                else:
                    existing_kind = existing[0]
                if existing_kind == "ext":
                    ext_count += 1
                elif existing_kind == "int":
                    int_count += 1
                else:
                    uncovered += 1
            parts: list[str] = []
            if ext_count or int_count:
                parts.append(f"hardware trip lines active: ext={ext_count} int={int_count}")
            if uncovered:
                parts.append(f"{uncovered} group(s) without hardware trip line (watchdog only)")
            self.trip_line_status = "; ".join(parts)
        except Exception as exc:
            self.trip_line_status = f"error: {exc}"

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

    _RUP_NAMES = ["RUp", "RUP"]
    _RDOWN_NAMES = ["RDWn", "RDown", "RDWN"]
    _PDWN_NAMES = ["PDWN", "PDwn"]
    # CAEN-standard PDWn enum when the device does not expose option names.
    _PDWN_FALLBACK_BY_INDEX = {0: "KILL", 1: "RAMP"}
    _RAMP_TOLERANCE = 1e-6

    def _pdown_options(self, slot: int, channel: int) -> list[str] | None:
        """Enum option names in device index order, upper-cased; None if absent."""
        bridge = self._ensure_bridge()
        if not hasattr(bridge, "Device_get_ch_param_prop"):
            return None
        for name in self._PDWN_NAMES:
            try:
                prop = bridge.Device_get_ch_param_prop(slot, channel, name)
            except Exception:
                continue
            opts = None
            for key in ("enum", "Enum", "options"):
                opts = prop.get(key) if isinstance(prop, dict) else getattr(prop, key, None)
                if opts:
                    break
            if opts:
                return [str(o).strip().upper() for o in opts]
        return None

    def _pdown_index_to_name(self, slot: int, channel: int, value: Any) -> str:
        try:
            idx = int(value)
        except (TypeError, ValueError):
            return str(value).strip().upper()  # already a mode name
        options = self._pdown_options(slot, channel)
        if options and 0 <= idx < len(options):
            return options[idx]
        return self._PDWN_FALLBACK_BY_INDEX.get(idx, str(value))

    def _pdown_name_to_value(self, slot: int, channel: int, mode: Any) -> Any:
        key = str(mode).strip().upper()
        options = self._pdown_options(slot, channel)
        if options and key in options:
            return options.index(key)
        for idx, name in self._PDWN_FALLBACK_BY_INDEX.items():
            if name == key:
                return idx
        return mode  # last resort: pass through unchanged

    def set_pdown_mode(self, slot: int, channel: int, mode: str) -> None:
        """Write PDWn as the device's numeric enum value for the mode name."""
        self._set_pdown_value(int(slot), int(channel), self._pdown_name_to_value(int(slot), int(channel), mode))
    # Status bits per CAEN convention: 6 = external trip, 8 = internal trip.
    TRIP_STATUS_MASK = (1 << 6) | (1 << 8)

    def _get_numeric_param_any_strict(self, slot: int, channel: int, names: list[str]) -> float:
        found = self._get_numeric_param_any(int(slot), int(channel), names)
        if found is None:
            raise RuntimeError(f"failed to read {names[0]} for channel {slot}:{channel}")
        return float(found[0])

    def _set_param_any_strict(self, slot: int, channel: int, names: list[str], value: float) -> str:
        applied = self._set_param_any(int(slot), int(channel), names, float(value))
        if applied is None:
            raise RuntimeError(f"failed to write {names[0]}={value} for channel {slot}:{channel}")
        return applied

    def _read_group_ramps(self, group: list[tuple[int, int]]) -> dict[tuple[int, int], tuple[float, float]]:
        ramps: dict[tuple[int, int], tuple[float, float]] = {}
        for slot, channel in group:
            rup = self._get_numeric_param_any_strict(slot, channel, self._RUP_NAMES)
            rdown = self._get_numeric_param_any_strict(slot, channel, self._RDOWN_NAMES)
            ramps[(slot, channel)] = (rup, rdown)
        return ramps

    def _get_pdown_value(self, slot: int, channel: int) -> Any | None:
        bridge = self._ensure_bridge()
        for name in self._PDWN_NAMES:
            try:
                values = bridge.Device_get_ch_param(int(slot), [int(channel)], name)
                if isinstance(values, list) and values:
                    return values[0]
            except Exception:
                continue
        return None

    def _set_pdown_value(self, slot: int, channel: int, value: Any) -> None:
        last_exc: Exception | None = None
        for name in self._PDWN_NAMES:
            try:
                self.set_channel_param(int(slot), int(channel), name, value)
                return
            except Exception as exc:
                last_exc = exc
        raise RuntimeError(f"failed to write PDWN for channel {slot}:{channel}") from last_exc

    def _channels_span_mixed_polarity(self, channels: set[tuple[int, int]]) -> bool:
        polarities = {self._slot_is_negative(int(slot)) for slot, _channel in channels}
        return len(polarities) > 1

    def _sync_link_ramps(self, channels: set[tuple[int, int]]) -> dict[str, float]:
        group = sorted({(int(s), int(c)) for (s, c) in channels})
        if len(group) < 2:
            return {}

        ramps = self._read_group_ramps(group)
        target_rup = min(rup for rup, _rdown in ramps.values())
        target_rdown = min(rdown for _rup, rdown in ramps.values())

        # In a mixed-polarity group a joint shift runs RUp on one channel
        # against RDWn on another, so every ramp value must be identical;
        # take the slowest of all of them.
        if self._channels_span_mixed_polarity(set(group)):
            target_rup = target_rdown = min(target_rup, target_rdown)

        for slot, channel in group:
            self._set_param_any_strict(slot, channel, self._RUP_NAMES, target_rup)
            self._set_param_any_strict(slot, channel, self._RDOWN_NAMES, target_rdown)

        return {"rup": float(target_rup), "rdown": float(target_rdown)}

    def _ensure_group_ramps_synced(self, channels: set[tuple[int, int]]) -> dict[str, float] | None:
        """Verify ramp equality across a linked group before a move.

        If the read-back values drifted since link creation, re-sync the
        whole group to the slowest value and return the applied updates;
        return None when the group was already consistent.
        """
        group = sorted({(int(s), int(c)) for (s, c) in channels})
        if len(group) < 2:
            return None
        ramps = self._read_group_ramps(group)
        rup_values = [rup for rup, _rdown in ramps.values()]
        rdown_values = [rdown for _rup, rdown in ramps.values()]
        if self._channels_span_mixed_polarity(set(group)):
            all_values = rup_values + rdown_values
            consistent = max(all_values) - min(all_values) <= self._RAMP_TOLERANCE
        else:
            consistent = (
                max(rup_values) - min(rup_values) <= self._RAMP_TOLERANCE
                and max(rdown_values) - min(rdown_values) <= self._RAMP_TOLERANCE
            )
        if consistent:
            return None
        return self._sync_link_ramps(set(group))

    def _sync_link_pdown(
        self,
        channels: set[tuple[int, int]],
        *,
        adopt_from: tuple[int, int],
        strict: bool = True,
    ) -> Any | None:
        """Make PDWN identical across a linked group, adopting one channel's mode.

        Returns the adopted mode if any channel was changed, None when the
        group was already consistent or PDWN is unreadable. With strict=False
        write failures are swallowed (used on power-off, which must proceed).
        """
        group = sorted({(int(s), int(c)) for (s, c) in channels})
        if len(group) < 2:
            return None
        target = self._get_pdown_value(int(adopt_from[0]), int(adopt_from[1]))
        if target is None:
            return None
        target_norm = str(target).strip().lower()
        changed = False
        for slot, channel in group:
            current = self._get_pdown_value(slot, channel)
            if current is not None and str(current).strip().lower() == target_norm:
                continue
            try:
                self._set_pdown_value(slot, channel, target)
            except Exception:
                if strict:
                    raise
                continue
            changed = True
        return target if changed else None

    def apply_linked_ramp(self, slot: int, channel: int, field: str, value: float) -> dict[str, float]:
        """Propagate a rup/rdown edit across the linked group.

        Mixed-polarity groups keep RUp and RDWn identical, so an edit to
        either field is applied to both parameters on every linked channel.
        """
        name = str(field).strip().lower()
        if name not in ("rup", "rdown"):
            raise ValueError(f"unsupported ramp field: {field}")
        # GUI values are signed (slew convention); the group shares magnitudes.
        magnitude = abs(float(value))
        linked = self.get_linked_channels_recursive(int(slot), int(channel))
        if self._channels_span_mixed_polarity(linked):
            self.set_param_for_channels(linked, "RUp", magnitude)
            self.set_param_for_channels(linked, "RDWn", magnitude)
            return {"rup": magnitude, "rdown": magnitude}
        self.set_param_for_channels(linked, "RUp" if name == "rup" else "RDWn", magnitude)
        return {name: magnitude}

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
                    raise ChannelError(
                        source[0], source[1],
                        f"conflicting linked targets for channel {source[0]}:{source[1]}",
                    )
        return targets

    def _validate_vset_targets_in_range(self, targets: dict[tuple[int, int], float]) -> None:
        for (slot, channel), target_vset in sorted(targets.items()):
            target = float(target_vset)
            limits = self.fetch_channel_constraints(int(slot), int(channel))
            lo = limits.get("vset_min")
            hi = limits.get("vset_max")
            if lo is not None and hi is not None and not (float(lo) <= target <= float(hi)):
                raise ChannelError(
                    slot, channel,
                    "resulted Vset out of range for "
                    f"{slot}:{channel} (target={target:.6g}, range={float(lo):.6g}..{float(hi):.6g})",
                )
            # Reject targets the hardware would clamp to SVMax; a silent
            # clamp changes the achieved difference between linked channels.
            svmax = self._get_numeric_param_any(int(slot), int(channel), ["SVMax", "SVMAX"])
            if svmax is not None:
                backend_target = abs(float(self._to_backend_voltage(int(slot), "V0Set", target)))
                if backend_target > float(svmax[0]) + 1e-6:
                    raise ChannelError(
                        slot, channel,
                        "resulted Vset exceeds SVMax for "
                        f"{slot}:{channel} (target={target:.6g}, svmax={float(svmax[0]):.6g})",
                    )

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
                raise ChannelError(
                    slot, channel,
                    f"linked queue rejected: initiator {initiator[0]}:{initiator[1]} is ON "
                    f"but linked channel {slot}:{channel} is OFF",
                )
            if (not init_on) and ch_on:
                raise ChannelError(
                    slot, channel,
                    f"linked queue rejected: initiator {initiator[0]}:{initiator[1]} is OFF "
                    f"but linked channel {slot}:{channel} is ON",
                )

    def _set_channel_power(self, slot: int, channel: int, enabled: bool) -> None:
        self.set_channel_param(int(slot), int(channel), "Pw", 1 if bool(enabled) else 0)
        key = (int(slot), int(channel))
        state = self._get_channel_state(int(slot), int(channel))
        state["power"] = bool(enabled)
        self._channel_state[key] = state

    def set_power_for_channels(
        self,
        channels: set[tuple[int, int]],
        enabled: bool,
        *,
        initiator: tuple[int, int] | None = None,
    ) -> dict[str, Any]:
        group = sorted({(int(s), int(c)) for (s, c) in channels})
        info: dict[str, Any] = {"ramp_resync": None, "pdown_synced": None}
        if len(group) > 1:
            if enabled:
                info["ramp_resync"] = self._ensure_group_ramps_synced(set(group))
            else:
                # Power-off must proceed even if the pre-checks fail.
                try:
                    info["ramp_resync"] = self._ensure_group_ramps_synced(set(group))
                except Exception as exc:
                    info["warning"] = f"ramp check skipped: {exc}"
                adopt = initiator if initiator is not None else group[0]
                info["pdown_synced"] = self._sync_link_pdown(
                    set(group), adopt_from=(int(adopt[0]), int(adopt[1])), strict=False
                )
        for slot, channel in group:
            self._set_channel_power(slot, channel, bool(enabled))
        return info

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
            try:
                self.set_channel_param(slot, channel, "V0Set", float(target_v))
            except Exception as exc:
                # Roll already-moved channels back to their previous set
                # values so the group does not settle at a wrong difference.
                rollback_failed: list[str] = []
                for r_slot, r_channel in reversed(executed):
                    r_key = (int(r_slot), int(r_channel))
                    try:
                        self.set_channel_param(r_slot, r_channel, "V0Set", float(pre_vsets[r_key]))
                        r_state = self._get_channel_state(r_slot, r_channel)
                        r_state["vset"] = float(pre_vsets[r_key])
                        self._channel_state[r_key] = r_state
                    except Exception:
                        rollback_failed.append(f"{r_slot}:{r_channel}")
                detail = ""
                if executed:
                    detail = f"; rolled back {len(executed) - len(rollback_failed)}/{len(executed)} moved channels"
                    if rollback_failed:
                        detail += f", rollback FAILED for {', '.join(rollback_failed)}"
                raise RuntimeError(f"V0Set write failed for {slot}:{channel}: {exc}{detail}") from exc
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
            # Signed by polarity, consistent with vset and the GUI display.
            payload["vmon"] = self._to_ui_voltage(
                slot, "VMon", bridge.Device_get_ch_param(slot, [channel], "VMon")[0]
            )
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
        if "status" in payload:
            key = (int(slot), int(channel))
            state = dict(self._channel_state.get(key) or {})
            state["status"] = payload["status"]
            self._channel_state[key] = state
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
        )
        for param_name, key in params:
            try:
                value = bridge.Device_get_ch_param(slot, [channel], param_name)[0]
                if param_name in ("V0Set", "SVMax", "RUp"):
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
                    raw = bridge.Device_get_ch_param(slot, [channel], rdown_name)[0]
                    payload["rdown"] = self._to_ui_voltage(slot, rdown_name, raw)
                    break
                except Exception:
                    continue
        if "pdown" not in payload:
            for pdown_name in self._PDWN_NAMES:
                try:
                    raw = bridge.Device_get_ch_param(slot, [channel], pdown_name)[0]
                    payload["pdown"] = self._pdown_index_to_name(slot, channel, raw)
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

    def _group_ramping_channels(self, channels: set[tuple[int, int]]) -> list[tuple[int, int]]:
        ramping: list[tuple[int, int]] = []
        for slot, channel in sorted(channels):
            status = (self._channel_state.get((int(slot), int(channel))) or {}).get("status")
            try:
                bits = int(status)
            except Exception:
                continue
            if bits & 0b110:  # RUP or RDWN active
                ramping.append((int(slot), int(channel)))
        return ramping

    def apply_linked_vset(self, slot: int, channel: int, requested_vset: float) -> dict[str, Any]:
        key = (int(slot), int(channel))
        current_rule = self._link_rules.get(key)
        previous_rule = current_rule
        updated_rule = False
        if current_rule is not None:
            reference, _offset = current_rule
            ref_state = self._get_channel_state(reference[0], reference[1])
            ref_vset = float(ref_state.get("vset", 0.0))
            self._link_rules[key] = (reference, float(requested_vset) - ref_vset)
            updated_rule = True
        try:
            targets = self._build_linked_targets(requested_values={key: float(requested_vset)})
            self._validate_vset_targets_in_range(targets)
            self._validate_linked_power_consistency(initiator=key, affected=set(targets.keys()))
            ramp_resync = self._ensure_group_ramps_synced(set(targets.keys()))
            pdown_synced = self._sync_link_pdown(set(targets.keys()), adopt_from=key, strict=False)
            ramping = self._group_ramping_channels(set(targets.keys()))
            self._execute_vset_plan(targets)
        except Exception:
            if updated_rule:
                if previous_rule is None:
                    self._link_rules.pop(key, None)
                else:
                    self._link_rules[key] = previous_rule
            raise
        return {"ramp_resync": ramp_resync, "ramping": ramping, "pdown_synced": pdown_synced}

    def apply_linked_offset(self, slot: int, channel: int, offset: float) -> dict[str, Any]:
        key = (int(slot), int(channel))
        current = self._link_rules.get(key)
        if current is None:
            raise RuntimeError(f"channel {slot}:{channel} has no reference link")
        previous_rule = current
        reference, _ = current
        self._link_rules[key] = (reference, float(offset))
        try:
            ref_state = self._get_channel_state(reference[0], reference[1])
            target_vset = float(ref_state.get("vset", 0.0)) + float(offset)
            targets = self._build_linked_targets(requested_values={key: target_vset})
            self._validate_vset_targets_in_range(targets)
            self._validate_linked_power_consistency(initiator=key, affected=set(targets.keys()))
            ramp_resync = self._ensure_group_ramps_synced(set(targets.keys()))
            pdown_synced = self._sync_link_pdown(set(targets.keys()), adopt_from=key, strict=False)
            ramping = self._group_ramping_channels(set(targets.keys()))
            self._execute_vset_plan(targets)
        except Exception:
            self._link_rules[key] = previous_rule
            raise
        return {"ramp_resync": ramp_resync, "ramping": ramping, "pdown_synced": pdown_synced}

    def apply_linked_bulk(self, sets: list[dict]) -> dict[str, Any]:
        """Apply several linked vset/offset changes atomically.

        Each entry is {"slot", "ch", "vset": float} (a master setpoint) or
        {"slot", "ch", "offset": float} (relative level of a linked channel).
        All requested changes are seeded before validation, so a valid final
        state is not rejected at an intermediate single-channel step. Link
        rules are rolled back on any failure.
        """
        if not sets:
            raise ValueError("apply_linked_bulk requires at least one entry")
        keys = [(int(s["slot"]), int(s["ch"])) for s in sets]
        snapshot = {k: self._link_rules.get(k) for k in keys}
        requested_values: dict[tuple[int, int], float] = {}
        try:
            # vset entries are direct master requests.
            for s in sets:
                if "vset" in s:
                    requested_values[(int(s["slot"]), int(s["ch"]))] = float(s["vset"])
                elif "offset" not in s:
                    raise ValueError(
                        f"bulk entry for {s.get('slot')}:{s.get('ch')} needs 'vset' or 'offset'"
                    )
            # offset entries update the link rule and seed the resulting target
            # (from its reference's new value if that reference is also set here,
            # otherwise its current value).
            for s in sets:
                if "offset" not in s:
                    continue
                key = (int(s["slot"]), int(s["ch"]))
                current = self._link_rules.get(key)
                if current is None:
                    raise RuntimeError(f"channel {key[0]}:{key[1]} has no reference link")
                reference, _ = current
                offset = float(s["offset"])
                self._link_rules[key] = (reference, offset)
                ref_val = requested_values.get(reference)
                if ref_val is None:
                    ref_val = float(self._get_channel_state(reference[0], reference[1]).get("vset", 0.0))
                target = ref_val + offset
                prior = requested_values.get(key)
                if prior is not None and abs(prior - target) > 1e-6:
                    raise RuntimeError(f"conflicting bulk request for {key[0]}:{key[1]}")
                requested_values[key] = target
            targets = self._build_linked_targets(requested_values=requested_values)
            self._validate_vset_targets_in_range(targets)
            initiator = keys[0]
            self._validate_linked_power_consistency(initiator=initiator, affected=set(targets.keys()))
            ramp_resync = self._ensure_group_ramps_synced(set(targets.keys()))
            pdown_synced = self._sync_link_pdown(set(targets.keys()), adopt_from=initiator, strict=False)
            ramping = self._group_ramping_channels(set(targets.keys()))
            self._execute_vset_plan(targets)
        except Exception:
            for k, rule in snapshot.items():
                if rule is None:
                    self._link_rules.pop(k, None)
                else:
                    self._link_rules[k] = rule
            raise
        return {
            "ramp_resync": ramp_resync,
            "ramping": ramping,
            "pdown_synced": pdown_synced,
            "targets": {f"{s}:{c}": float(v) for (s, c), v in targets.items()},
        }

    def apply_linked_power(self, slot: int, channel: int, enabled: bool) -> None:
        key = (int(slot), int(channel))
        self._set_channel_power(key[0], key[1], bool(enabled))

    def check_trip_and_power_off_partners(
        self, slot: int, channel: int, status: Any
    ) -> list[tuple[int, int]] | None:
        """React to a trip on a linked channel by powering off its partners.

        Returns the list of partners powered off when a trip was handled
        (possibly empty), or None when the status shows no trip or the
        channel is not linked.
        """
        try:
            bits = int(status)
        except Exception:
            return None
        if not bits & self.TRIP_STATUS_MASK:
            return None
        key = (int(slot), int(channel))
        # The tripped channel is off in hardware; reflect that in the cache
        # even when it has no linked partners.
        tripped_state = self._get_channel_state(key[0], key[1])
        tripped_state["power"] = False
        self._channel_state[key] = tripped_state
        linked = self.get_linked_channels_recursive(key[0], key[1])
        if len(linked) < 2:
            return None
        powered_off: list[tuple[int, int]] = []
        for l_slot, l_channel in sorted(linked):
            if (l_slot, l_channel) == key:
                continue
            state = self._get_channel_state(l_slot, l_channel)
            if not self._to_bool(state.get("power", False)):
                continue
            self._set_channel_power(l_slot, l_channel, False)
            powered_off.append((l_slot, l_channel))
        return powered_off
