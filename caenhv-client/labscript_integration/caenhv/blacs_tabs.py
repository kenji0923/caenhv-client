"""BLACS tab that reuses caenhv_client.gui.main_window.MainWindow."""

from __future__ import annotations

from pathlib import Path

from blacs.device_base_class import (
    MODE_BUFFERED,
    MODE_MANUAL,
    DeviceTab,
    define_state,
)

try:
    from ...gui.main_window import MainWindow
except Exception:
    from caenhv_client.gui.main_window import MainWindow


class CAENHVTab(DeviceTab):
    def __init__(self, *args, **kwargs):
        self.ui: MainWindow | None = None
        self._poll_tick_count = 0
        DeviceTab.__init__(self, *args, **kwargs)

    def initialise_GUI(self):
        root_dir = Path(__file__).resolve().parents[3]
        self.ui = MainWindow(root_dir)
        self.get_tab_layout().addWidget(self.ui)
        self._wire_ui_signals()
        self.supports_remote_value_check(False)
        self.supports_smart_programming(True)
        self.statemachine_timeout_add(1000, self._poll_tick)
        DeviceTab.initialise_GUI(self)

    def initialise_workers(self):
        ct = self.settings["connection_table"].find_by_name(self.device_name)
        props = dict(getattr(ct, "properties", {}) or {})
        added = dict(props.get("added_properties", {}) or {})
        kwargs = {
            "server_host": str(added.get("server_host", "127.0.0.1")),
            "server_port": int(added.get("server_port", 50250)),
            "client_name": str(added.get("client_name", f"blacs_{self.device_name}")),
            "channels": list(added.get("channels", [])),
            "bridge_search_paths": list(added.get("bridge_search_paths", [])),
        }
        self.create_worker(
            "main_worker",
            "caenhv_client.labscript_integration.caenhv.blacs_workers.CAENHVWorker",
            kwargs,
        )
        self.primary_worker = "main_worker"

    def _wire_ui_signals(self) -> None:
        assert self.ui is not None
        self.ui.sig_connect_requested.connect(self._on_connect_requested)
        self.ui.sig_disconnect_requested.connect(self._on_disconnect_requested)
        self.ui.sig_refresh_resources_requested.connect(self._on_refresh_resources_requested)
        self.ui.sig_resource_action_requested.connect(self._on_resource_action_requested)
        self.ui.sig_label_changed.connect(self._on_label_changed)
        self.ui.sig_link_rule_requested.connect(self._on_link_rule_requested)
        self.ui.sig_reference_offset_changed.connect(self._on_reference_offset_requested)
        self.ui.sig_channel_vset_requested.connect(self._on_vset_requested)
        self.ui.sig_channel_power_toggled.connect(self._on_power_requested)
        self.ui.sig_rup_changed.connect(
            lambda slot, channel, value: self._on_param_requested(slot, channel, "RUp", value)
        )
        self.ui.sig_rdown_changed.connect(
            lambda slot, channel, value: self._on_param_requested(slot, channel, "RDWn", value)
        )
        self.ui.sig_trip_changed.connect(
            lambda slot, channel, value: self._on_param_requested(slot, channel, "Trip", value)
        )
        self.ui.sig_svmax_changed.connect(
            lambda slot, channel, value: self._on_param_requested(slot, channel, "SVMax", value)
        )
        self.ui.sig_iset_changed.connect(
            lambda slot, channel, value: self._on_param_requested(slot, channel, "I0Set", value)
        )
        self.ui.sig_pdown_changed.connect(
            lambda slot, channel, value: self._on_param_requested(slot, channel, "PDWN", value)
        )

    @define_state(MODE_MANUAL, True, True)
    def _on_connect_requested(self, host: str, port: int, client_name: str, force: bool):
        assert self.ui is not None
        payload = yield self.queue_work(
            self.primary_worker,
            "connect_client",
            server_host=str(host),
            server_port=int(port),
            client_name=str(client_name),
            force=bool(force),
        )
        self.ui.on_connected(payload)
        rows = yield self.queue_work(self.primary_worker, "refresh_resources")
        self.ui.on_resources_updated(rows)

    @define_state(MODE_MANUAL | MODE_BUFFERED, True, True)
    def _on_disconnect_requested(self):
        assert self.ui is not None
        yield self.queue_work(self.primary_worker, "disconnect_client")
        self.ui.on_disconnected()

    @define_state(MODE_MANUAL | MODE_BUFFERED, True, True)
    def _on_refresh_resources_requested(self):
        assert self.ui is not None
        rows = yield self.queue_work(self.primary_worker, "refresh_resources")
        self.ui.on_resources_updated(rows)

    @define_state(MODE_MANUAL | MODE_BUFFERED, True, True)
    def _on_resource_action_requested(self, action: str, resource: str):
        assert self.ui is not None
        if str(action).strip().lower() == "acquire":
            ok = yield self.queue_work(self.primary_worker, "acquire_resource", resource=resource)
            self.ui.append_response_log(f"acquire resource={resource} ok={ok}")
        elif str(action).strip().lower() == "release":
            ok = yield self.queue_work(self.primary_worker, "release_resource", resource=resource)
            self.ui.append_response_log(f"release resource={resource} ok={ok}")
        rows = yield self.queue_work(self.primary_worker, "refresh_resources")
        self.ui.on_resources_updated(rows)

    @define_state(MODE_MANUAL | MODE_BUFFERED, True, True)
    def _on_label_changed(self, slot: int, channel: int, label: str):
        assert self.ui is not None
        yield self.queue_work(
            self.primary_worker,
            "set_channel_name",
            slot=int(slot),
            channel=int(channel),
            label=str(label),
        )
        rows = yield self.queue_work(self.primary_worker, "refresh_resources")
        self.ui.on_resources_updated(rows)

    @define_state(MODE_MANUAL | MODE_BUFFERED, True, True)
    def _on_link_rule_requested(self, slot: int, channel: int, reference: str, offset: float):
        assert self.ui is not None
        ramp_updates = yield self.queue_work(
            self.primary_worker,
            "set_link_rule",
            slot=int(slot),
            channel=int(channel),
            reference=str(reference),
            offset=float(offset),
        )
        if isinstance(ramp_updates, dict):
            parsed = self.ui._parse_reference_key(str(reference))
            if parsed is not None:
                self.ui.apply_link_ramp_values([(int(slot), int(channel)), (int(parsed[0]), int(parsed[1]))], ramp_updates)

    @define_state(MODE_MANUAL | MODE_BUFFERED, True, True)
    def _on_reference_offset_requested(self, slot: int, channel: int, offset: float):
        yield self.queue_work(
            self.primary_worker,
            "set_reference_offset",
            slot=int(slot),
            channel=int(channel),
            offset=float(offset),
        )

    @define_state(MODE_MANUAL | MODE_BUFFERED, True, True)
    def _on_vset_requested(self, slot: int, channel: int, value: float):
        yield self.queue_work(
            self.primary_worker,
            "apply_vset",
            slot=int(slot),
            channel=int(channel),
            value=float(value),
        )

    @define_state(MODE_MANUAL | MODE_BUFFERED, True, True)
    def _on_power_requested(self, slot: int, channel: int, enabled: bool):
        yield self.queue_work(
            self.primary_worker,
            "set_channel_power",
            slot=int(slot),
            channel=int(channel),
            enabled=bool(enabled),
        )

    @define_state(MODE_MANUAL | MODE_BUFFERED, True, True)
    def _on_param_requested(self, slot: int, channel: int, name: str, value):
        yield self.queue_work(
            self.primary_worker,
            "set_channel_param",
            slot=int(slot),
            channel=int(channel),
            name=str(name),
            value=value,
        )

    @define_state(MODE_MANUAL | MODE_BUFFERED, True, True)
    def _poll_tick(self):
        assert self.ui is not None
        if not getattr(self.ui, "_connected", False):
            return
        self._poll_tick_count += 1
        if self._poll_tick_count % 5 == 0:
            rows = yield self.queue_work(self.primary_worker, "refresh_resources")
            self.ui.on_resources_updated(rows)
        for slot, channel in list(self.ui._channel_widgets.keys()):
            payload = yield self.queue_work(
                self.primary_worker,
                "refresh_channel_snapshot",
                slot=int(slot),
                channel=int(channel),
            )
            self.ui.on_channel_updated(int(slot), int(channel), payload)
