from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from PyQt5 import QtCore, QtWidgets

from caenhv_client.gui.main_window import MainWindow
from caenhv_client.worker.client_worker import ClientWorker


class StandaloneMainWindow(MainWindow):
    """Standalone controller that binds MainWindow UI signals to ClientWorker calls."""

    def __init__(self, root_dir: Path, parent=None) -> None:
        super().__init__(root_dir, parent)
        self._settings = self._create_settings("caenhv_client_standalone")
        self._widget_init_done: set[tuple[int, int]] = set()
        self._settings_check_running = False
        self._last_settings_check_monotonic = 0.0
        self._last_ui_activity_monotonic = time.monotonic()
        self._poll_timer = QtCore.QTimer(self)
        self._poll_timer.setInterval(1000)
        self._poll_timer.timeout.connect(self._slot_poll_tick)
        bridge_path = root_dir / "caenhv_devman" / "generated_bridge"
        self._worker = ClientWorker(bridge_search_paths=[bridge_path])
        self._wire_standalone_slots()
        self._load_connection_inputs()
        app = QtWidgets.QApplication.instance()
        if app is not None:
            app.installEventFilter(self)

    def _load_connection_inputs(self) -> None:
        host = str(self._settings.value("connection/server_host", "127.0.0.1"))
        client_name = str(self._settings.value("connection/client_name", ""))
        force = str(self._settings.value("connection/force", "false")).strip().lower() in (
            "1",
            "true",
            "yes",
            "on",
        )
        self.lineEditServerHost.setText(host)
        self.lineEditClientName.setText(client_name)
        if hasattr(self, "checkBoxForceConnect"):
            self.checkBoxForceConnect.setChecked(force)
        if hasattr(self, "checkBoxPeriodicSettingsCheck"):
            enabled = str(self._settings.value("settings_check/enabled", "false")).strip().lower() in (
                "1",
                "true",
                "yes",
                "on",
            )
            self.checkBoxPeriodicSettingsCheck.setChecked(enabled)
        if hasattr(self, "spinBoxSettingsCheckIntervalSec"):
            try:
                interval = int(self._settings.value("settings_check/interval_sec", 5))
            except Exception:
                interval = 5
            self.spinBoxSettingsCheckIntervalSec.setValue(max(1, interval))

    def _save_connection_inputs(self) -> None:
        self._settings.setValue("connection/server_host", self.lineEditServerHost.text().strip())
        self._settings.setValue("connection/client_name", self.lineEditClientName.text().strip())
        if hasattr(self, "checkBoxForceConnect"):
            self._settings.setValue("connection/force", bool(self.checkBoxForceConnect.isChecked()))
        if hasattr(self, "checkBoxPeriodicSettingsCheck"):
            self._settings.setValue(
                "settings_check/enabled",
                bool(self.checkBoxPeriodicSettingsCheck.isChecked()),
            )
        if hasattr(self, "spinBoxSettingsCheckIntervalSec"):
            self._settings.setValue(
                "settings_check/interval_sec",
                int(self.spinBoxSettingsCheckIntervalSec.value()),
            )

    def closeEvent(self, event) -> None:
        self._save_connection_inputs()
        self._poll_timer.stop()
        try:
            if getattr(self, "_connected", False):
                self._worker.disconnect_client()
                self.on_disconnected()
        except Exception as exc:
            self.append_response_log(f"ERROR: {exc}")
        super().closeEvent(event)

    def _wire_standalone_slots(self) -> None:
        self.sig_connect_requested.connect(self._slot_connect_worker)
        self.sig_disconnect_requested.connect(self._slot_disconnect_worker)
        self.sig_refresh_resources_requested.connect(self._slot_refresh_resources)
        self.sig_save_status_requested.connect(self._slot_save_status)
        self.sig_load_status_requested.connect(self._slot_load_status)
        self.sig_resource_action_requested.connect(self._slot_resource_action)

        self.sig_channel_vset_requested.connect(self._slot_channel_vset)
        self.sig_channel_power_toggled.connect(self._slot_channel_power)
        self.sig_rup_changed.connect(self._slot_rup)
        self.sig_rdown_changed.connect(self._slot_rdown)
        self.sig_trip_changed.connect(self._slot_trip)
        self.sig_svmax_changed.connect(self._slot_svmax)
        self.sig_iset_changed.connect(self._slot_iset)
        self.sig_pdown_changed.connect(self._slot_pdown)

        self.sig_reference_changed.connect(self._slot_reference_changed)
        self.sig_reference_offset_changed.connect(self._slot_reference_offset_changed)
        self.sig_link_rule_requested.connect(self._slot_link_rule_requested)
        self.sig_label_changed.connect(self._slot_label_changed)

    @QtCore.pyqtSlot(str, int, str, bool)
    def _slot_connect_worker(self, host: str, port: int, client_name: str, force: bool) -> None:
        try:
            self._save_connection_inputs()
            payload = self._worker.connect_client(host, port, client_name, force=bool(force))
            self.on_connected(payload)
            self._slot_refresh_resources()
            self._poll_timer.start()
        except Exception as exc:
            self.append_response_log(f"ERROR: {exc}")

    @QtCore.pyqtSlot()
    def _slot_disconnect_worker(self) -> None:
        try:
            self._poll_timer.stop()
            self._worker.disconnect_client()
            self._widget_init_done.clear()
            self.on_disconnected()
        except Exception as exc:
            self.append_response_log(f"ERROR: {exc}")

    @QtCore.pyqtSlot()
    def _slot_refresh_resources(self) -> None:
        try:
            rows = self._worker.refresh_resources_cached()
            self.on_resources_updated(rows)
            self._sync_channel_widgets_from_rows(rows)
        except Exception as exc:
            self.append_response_log(f"ERROR: {exc}")

    @QtCore.pyqtSlot()
    def _slot_poll_tick(self) -> None:
        try:
            for slot, ch in list(self._channel_widgets.keys()):
                payload = self._worker.refresh_channel_snapshot(int(slot), int(ch))
                self.on_channel_updated(int(slot), int(ch), payload)
            self._maybe_run_periodic_settings_check()
        except Exception as exc:
            self.append_response_log(f"ERROR: {exc}")

    def eventFilter(self, watched: QtCore.QObject, event: QtCore.QEvent) -> bool:
        if event.type() in (
            QtCore.QEvent.KeyPress,
            QtCore.QEvent.MouseButtonPress,
            QtCore.QEvent.MouseButtonDblClick,
            QtCore.QEvent.Wheel,
        ):
            self._last_ui_activity_monotonic = time.monotonic()
        return super().eventFilter(watched, event)

    def _is_settings_check_enabled(self) -> bool:
        return bool(
            hasattr(self, "checkBoxPeriodicSettingsCheck")
            and self.checkBoxPeriodicSettingsCheck.isChecked()
        )

    def _settings_check_interval_sec(self) -> float:
        if not hasattr(self, "spinBoxSettingsCheckIntervalSec"):
            return 5.0
        try:
            return float(max(1, int(self.spinBoxSettingsCheckIntervalSec.value())))
        except Exception:
            return 5.0

    def _maybe_run_periodic_settings_check(self) -> None:
        if not self._is_settings_check_enabled():
            return
        if not getattr(self, "_connected", False):
            return
        if self._settings_check_running:
            return
        interval = self._settings_check_interval_sec()
        now = time.monotonic()
        if now - self._last_ui_activity_monotonic < interval:
            return
        if now - self._last_settings_check_monotonic < interval:
            return
        self._settings_check_running = True
        try:
            self._run_periodic_settings_check_once()
        finally:
            self._last_settings_check_monotonic = time.monotonic()
            self._settings_check_running = False

    def _float_differs(self, a: Any, b: Any, *, atol: float = 1e-9) -> bool:
        try:
            return abs(float(a) - float(b)) > float(atol)
        except Exception:
            return str(a) != str(b)

    def _collect_local_widget_settings(self, widget) -> dict[str, Any]:
        settings: dict[str, Any] = {
            "vset": float(widget.doubleSpinBoxVset.value()),
            "power": 1 if bool(widget.checkBoxEnable.isChecked()) else 0,
            "rup": float(widget.doubleSpinBoxRup.value()),
            "rdown": float(widget.doubleSpinBoxRdown.value()),
            "trip": float(widget.doubleSpinBoxTrip.value()),
            "svmax": float(widget.doubleSpinBoxSVmax.value()),
            "pdown": str(widget.comboBoxPdownMode.currentText()),
            "label": str(widget.lineEditLabel.text()),
        }
        if hasattr(widget, "doubleSpinBoxIset"):
            settings["iset"] = float(widget.doubleSpinBoxIset.value())
        return settings

    def _format_channel_set(self, channels: set[tuple[int, int]]) -> str:
        return ", ".join(f"{int(slot)}:{int(ch)}" for slot, ch in sorted(channels))

    def _prompt_apply_deviation(
        self,
        *,
        slot: int,
        channel: int,
        field: str,
        local_value: Any,
        remote_value: Any,
        extra_lines: list[str] | None = None,
    ) -> str:
        text_lines = [
            f"Deviation detected at {slot}:{channel} for {field}.",
            f"Remote: {remote_value}",
            f"Local:  {local_value}",
        ]
        if extra_lines:
            text_lines.extend([str(line) for line in extra_lines if str(line).strip()])
        text_lines.append("")
        text_lines.append("Which value should be applied?")
        box = QtWidgets.QMessageBox(self)
        box.setIcon(QtWidgets.QMessageBox.Question)
        box.setWindowTitle("Settings Mismatch")
        box.setText("\n".join(text_lines))
        btn_remote = box.addButton("Use Remote", QtWidgets.QMessageBox.AcceptRole)
        btn_local = box.addButton("Use Local", QtWidgets.QMessageBox.DestructiveRole)
        btn_skip = box.addButton("Skip", QtWidgets.QMessageBox.RejectRole)
        box.exec_()
        clicked = box.clickedButton()
        if clicked is btn_remote:
            return "remote"
        if clicked is btn_local:
            return "local"
        if clicked is btn_skip:
            return "skip"
        return "skip"

    def _apply_remote_to_widget_field(self, widget, field: str, remote_value: Any) -> None:
        if field == "label":
            blocker = QtCore.QSignalBlocker(widget.lineEditLabel)
            _ = blocker
            widget.lineEditLabel.setText(str(remote_value))
            return
        if field == "vset":
            blocker = QtCore.QSignalBlocker(widget.doubleSpinBoxVset)
            _ = blocker
            widget.doubleSpinBoxVset.setValue(float(remote_value))
            return
        if field == "iset" and hasattr(widget, "doubleSpinBoxIset"):
            blocker = QtCore.QSignalBlocker(widget.doubleSpinBoxIset)
            _ = blocker
            widget.doubleSpinBoxIset.setValue(float(remote_value))
            return
        if field == "power":
            blocker = QtCore.QSignalBlocker(widget.checkBoxEnable)
            _ = blocker
            widget.checkBoxEnable.setChecked(bool(int(remote_value)))
            return
        if field == "rup":
            blocker = QtCore.QSignalBlocker(widget.doubleSpinBoxRup)
            _ = blocker
            widget.doubleSpinBoxRup.setValue(float(remote_value))
            return
        if field == "rdown":
            blocker = QtCore.QSignalBlocker(widget.doubleSpinBoxRdown)
            _ = blocker
            widget.doubleSpinBoxRdown.setValue(float(remote_value))
            return
        if field == "trip":
            blocker = QtCore.QSignalBlocker(widget.doubleSpinBoxTrip)
            _ = blocker
            widget.doubleSpinBoxTrip.setValue(float(remote_value))
            return
        if field == "svmax":
            blocker = QtCore.QSignalBlocker(widget.doubleSpinBoxSVmax)
            _ = blocker
            widget.doubleSpinBoxSVmax.setValue(float(remote_value))
            return
        if field == "pdown":
            blocker = QtCore.QSignalBlocker(widget.comboBoxPdownMode)
            _ = blocker
            text = str(remote_value)
            if widget.comboBoxPdownMode.findText(text) < 0:
                widget.comboBoxPdownMode.addItem(text)
            widget.comboBoxPdownMode.setCurrentText(text)

    def _handle_vset_remote_choice(
        self,
        slot: int,
        channel: int,
        remote_vset: float,
    ) -> None:
        widget = self._channel_widgets.get((int(slot), int(channel)))
        if widget is not None:
            self._apply_remote_to_widget_field(widget, "vset", remote_vset)
        payload: dict[str, Any] = {"vset": float(remote_vset)}
        reference = self._worker.get_link_reference(int(slot), int(channel))
        if reference is not None:
            ref_payload = self._worker.fetch_channel_settings(int(reference[0]), int(reference[1]))
            ref_vset = float(ref_payload.get("vset", 0.0))
            new_offset = float(remote_vset) - ref_vset
            self._worker.set_link_offset(int(slot), int(channel), new_offset)
            if widget is not None:
                widget.set_reference_offset(new_offset)
            payload["offset"] = float(new_offset)
        self._worker.update_cached_channel_settings(int(slot), int(channel), payload)

    def _run_periodic_settings_check_once(self) -> None:
        fields_to_check = ("label", "vset", "iset", "power", "rup", "rdown", "trip", "svmax", "pdown")
        handled: set[tuple[int, int, str]] = set()
        for slot, channel in sorted(self._channel_widgets.keys()):
            widget = self._channel_widgets.get((int(slot), int(channel)))
            if widget is None:
                continue
            local = self._collect_local_widget_settings(widget)
            remote = self._worker.fetch_channel_settings(int(slot), int(channel))
            for field in fields_to_check:
                token = (int(slot), int(channel), str(field))
                if token in handled:
                    continue
                if field not in local or field not in remote:
                    continue
                local_value = local[field]
                remote_value = remote[field]
                differs = (
                    self._float_differs(local_value, remote_value)
                    if field in ("vset", "iset", "power", "rup", "rdown", "trip", "svmax")
                    else str(local_value) != str(remote_value)
                )
                if not differs:
                    continue
                linked = self._worker.get_linked_channels_recursive(int(slot), int(channel))
                extra_lines: list[str] = []
                if field in ("rup", "rdown") and len(linked) > 1:
                    extra_lines.append(f"Affected linked channels: {self._format_channel_set(linked)}")
                if field == "vset":
                    reference = self._worker.get_link_reference(int(slot), int(channel))
                    if reference is not None:
                        ref_payload = self._worker.fetch_channel_settings(int(reference[0]), int(reference[1]))
                        ref_vset = float(ref_payload.get("vset", 0.0))
                        local_offset = float(local_value) - ref_vset
                        remote_offset = float(remote_value) - ref_vset
                        extra_lines.append(
                            f"Offset change if applied: local={local_offset:.6g}, remote={remote_offset:.6g}"
                        )
                choice = self._prompt_apply_deviation(
                    slot=int(slot),
                    channel=int(channel),
                    field=str(field),
                    local_value=local_value,
                    remote_value=remote_value,
                    extra_lines=extra_lines,
                )
                if choice == "skip":
                    handled.add(token)
                    continue
                if field == "label":
                    if choice == "local":
                        self._worker.set_channel_name(int(slot), int(channel), str(local_value))
                    else:
                        self._apply_remote_to_widget_field(widget, field, remote_value)
                        widget.apply_settings({"label": str(remote_value)})
                    handled.add(token)
                    continue
                if field == "vset":
                    if choice == "local":
                        self._worker.apply_linked_vset(int(slot), int(channel), float(local_value))
                        self._apply_cached_linked_widget_settings()
                    else:
                        self._handle_vset_remote_choice(int(slot), int(channel), float(remote_value))
                        widget.apply_settings({"vset": float(remote_value)})
                    handled.add(token)
                    continue
                if field in ("rup", "rdown") and len(linked) > 1:
                    param_name = "RUp" if field == "rup" else "RDWn"
                    if choice == "local":
                        self._worker.set_param_for_channels(linked, param_name, float(local_value))
                        if field == "rup":
                            self.apply_link_ramp_values(list(linked), {"rup": float(local_value)})
                        else:
                            self.apply_link_ramp_values(list(linked), {"rdown": float(local_value)})
                    else:
                        for ls, lc in sorted(linked):
                            r_payload = self._worker.fetch_channel_settings(int(ls), int(lc))
                            r_val = r_payload.get(field)
                            w = self._channel_widgets.get((int(ls), int(lc)))
                            if w is not None and r_val is not None:
                                self._apply_remote_to_widget_field(w, field, r_val)
                            self._worker.update_cached_channel_settings(int(ls), int(lc), r_payload)
                    for ls, lc in linked:
                        handled.add((int(ls), int(lc), str(field)))
                    continue
                if choice == "local":
                    if field == "power":
                        self._worker.set_channel_param(int(slot), int(channel), "Pw", int(local_value))
                    elif field == "pdown":
                        self._worker.set_channel_param(int(slot), int(channel), "PDWN", str(local_value))
                    elif field == "trip":
                        self._worker.set_channel_param(int(slot), int(channel), "Trip", float(local_value))
                    elif field == "svmax":
                        self._worker.set_channel_param(int(slot), int(channel), "SVMax", float(local_value))
                    elif field == "rup":
                        self._worker.set_channel_param(int(slot), int(channel), "RUp", float(local_value))
                    elif field == "rdown":
                        self._worker.set_channel_param(int(slot), int(channel), "RDWn", float(local_value))
                    elif field == "iset":
                        self._worker.set_channel_param(int(slot), int(channel), "I0Set", float(local_value))
                else:
                    self._apply_remote_to_widget_field(widget, field, remote_value)
                    widget.apply_settings({str(field): remote_value})
                    self._worker.update_cached_channel_settings(int(slot), int(channel), {str(field): remote_value})
                handled.add(token)

    def _refresh_linked_widget_settings(self) -> None:
        for slot, ch in list(self._channel_widgets.keys()):
            payload = self._worker.fetch_channel_settings(int(slot), int(ch))
            widget = self._channel_widgets.get((int(slot), int(ch)))
            if widget is not None:
                widget.apply_settings(payload)

    def _apply_cached_linked_widget_settings(self) -> None:
        for slot, ch in list(self._channel_widgets.keys()):
            payload = self._worker.get_cached_channel_settings(int(slot), int(ch))
            if not payload:
                continue
            widget = self._channel_widgets.get((int(slot), int(ch)))
            if widget is not None:
                widget.apply_settings(payload)
                offset = self._worker.get_link_offset(int(slot), int(ch))
                if offset is not None:
                    widget.set_reference_offset(float(offset))

    def _status_last_path_key(self) -> str:
        return f"{self._settings_scope()}/status_last_path"

    def _default_status_path(self) -> str:
        saved = str(self._settings.value(self._status_last_path_key(), "") or "").strip()
        if saved:
            return saved
        base = self._current_client_name() or "status"
        return str((Path.home() / f"{base}_status.json").resolve())

    def _serialize_widget_status(self, slot: int, channel: int) -> dict:
        widget = self._channel_widgets.get((int(slot), int(channel)))
        if widget is None:
            return {}
        settings = self._worker.fetch_channel_settings(int(slot), int(channel))
        return {
            "settings": settings,
            "reference": widget.get_reference_key(),
            "offset": float(widget.doubleSpinBoxReferenceOffset.value()),
            "verbose": bool(widget.is_verbose_visible()),
            "readout": {
                "vmon": widget.labelVmon.text(),
                "imon": widget.labelImon.text(),
                "status": widget.labelStatus.text(),
            },
        }

    def _build_status_snapshot(self) -> dict:
        channels: dict[str, dict] = {}
        for slot, channel in list(self._channel_widgets.keys()):
            key = self._channel_key(int(slot), int(channel))
            channels[key] = self._serialize_widget_status(int(slot), int(channel))
        return {
            "client_name": self._current_client_name(),
            "group_checked": {
                "connection": bool(self.groupBoxConnection.isChecked()),
                "resource": bool(self.groupBoxResource.isChecked()),
                "response": bool(self.groupBoxResponse.isChecked()),
                "channel": bool(self.groupBoxChannelSetting.isChecked()),
            },
            "splitter_sizes": list(self.splitterMainSections.sizes()),
            "order": [self._channel_key(w.slot, w.channel) for w in self._layout_channel_widgets()],
            "channels": channels,
        }

    def _apply_status_snapshot(self, snapshot: dict) -> int:
        group_checked = snapshot.get("group_checked", {})
        if isinstance(group_checked, dict):
            self.groupBoxConnection.setChecked(bool(group_checked.get("connection", self.groupBoxConnection.isChecked())))
            self.groupBoxResource.setChecked(bool(group_checked.get("resource", self.groupBoxResource.isChecked())))
            self.groupBoxResponse.setChecked(bool(group_checked.get("response", self.groupBoxResponse.isChecked())))
            self.groupBoxChannelSetting.setChecked(bool(group_checked.get("channel", self.groupBoxChannelSetting.isChecked())))

        splitter_sizes = snapshot.get("splitter_sizes")
        if isinstance(splitter_sizes, list) and splitter_sizes and len(splitter_sizes) == self.splitterMainSections.count():
            try:
                self.splitterMainSections.setSizes([int(x) for x in splitter_sizes])
            except Exception:
                pass

        channels = snapshot.get("channels", {})
        if not isinstance(channels, dict):
            channels = {}

        restored = 0
        for key, item in channels.items():
            if not isinstance(item, dict) or ":" not in str(key):
                continue
            slot_s, channel_s = str(key).split(":", 1)
            try:
                slot = int(slot_s)
                channel = int(channel_s)
            except Exception:
                continue
            widget = self._channel_widgets.get((slot, channel))
            if widget is None:
                continue

            settings = item.get("settings", {})
            if isinstance(settings, dict):
                widget.apply_settings(settings)
                self._worker.update_cached_channel_settings(slot, channel, settings)

            reference = item.get("reference")
            offset = float(item.get("offset", 0.0))
            ref_blocker = QtCore.QSignalBlocker(widget.comboBoxReference)
            off_blocker = QtCore.QSignalBlocker(widget.doubleSpinBoxReferenceOffset)
            _ = (ref_blocker, off_blocker)
            widget.set_reference_key(reference)
            widget.set_reference_offset(offset)

            parsed = self._parse_reference_key("None" if reference is None else str(reference))
            if parsed is not None:
                self._worker.set_link_rule(slot, channel, parsed, offset, sync_ramps=False)
            else:
                self._worker.set_link_rule(slot, channel, None, 0.0)

            widget.set_verbose_visible(bool(item.get("verbose", widget.is_verbose_visible())))

            readout = item.get("readout", {})
            if isinstance(readout, dict):
                if "vmon" in readout:
                    widget.labelVmon.setText(str(readout.get("vmon", "")))
                if "imon" in readout:
                    widget.labelImon.setText(str(readout.get("imon", "")))
                if "status" in readout:
                    widget.labelStatus.setText(str(readout.get("status", "")))
            restored += 1
        return restored

    @QtCore.pyqtSlot()
    def _slot_save_status(self) -> None:
        try:
            default_path = self._default_status_path()
            path, _flt = QtWidgets.QFileDialog.getSaveFileName(
                self,
                "Save Status JSON",
                default_path,
                "JSON Files (*.json);;All Files (*)",
            )
            if not path:
                self.append_response_log("status save canceled")
                return
            path_obj = Path(path)
            if path_obj.suffix.lower() != ".json":
                path_obj = path_obj.with_suffix(".json")
            snapshot = self._build_status_snapshot()
            path_obj.parent.mkdir(parents=True, exist_ok=True)
            path_obj.write_text(json.dumps(snapshot, indent=2, ensure_ascii=True), encoding="utf-8")
            self._settings.setValue(self._status_last_path_key(), str(path_obj))
            self._save_channel_ui_state()
            self.append_response_log(
                f"status saved file={path_obj} channels={len(snapshot.get('channels', {}))}"
            )
        except Exception as exc:
            self.append_response_log(f"ERROR: {exc}")

    @QtCore.pyqtSlot()
    def _slot_load_status(self) -> None:
        try:
            default_path = self._default_status_path()
            path, _flt = QtWidgets.QFileDialog.getOpenFileName(
                self,
                "Load Status JSON",
                default_path,
                "JSON Files (*.json);;All Files (*)",
            )
            if not path:
                self.append_response_log("status load canceled")
                return
            path_obj = Path(path)
            raw = path_obj.read_text(encoding="utf-8")
            snapshot = json.loads(raw)
            if not isinstance(snapshot, dict):
                self.append_response_log("status load: invalid snapshot")
                return
            restored = self._apply_status_snapshot(snapshot)
            self._settings.setValue(self._status_last_path_key(), str(path_obj))
            self._save_channel_ui_state()
            self.append_response_log(f"status loaded file={path_obj} channels={restored}")
        except Exception as exc:
            self.append_response_log(f"ERROR: {exc}")

    def _sync_channel_widgets_from_rows(self, rows: list[dict]) -> None:
        acquired: set[tuple[int, int]] = set()
        for row in rows:
            if str(row.get("action") or "").strip().lower() != "release":
                continue
            channel = row.get("channel")
            if channel is None:
                continue
            slot = int(row.get("slot"))
            ch = int(channel)
            key = (slot, ch)
            acquired.add(key)
            if key in self._widget_init_done:
                continue
            self._initialize_channel_widget(slot, ch)
            self._widget_init_done.add(key)
        self._widget_init_done.intersection_update(acquired)
        # During transient server/backend restart we can receive an empty acquired set.
        # Avoid clearing link topology in that case.
        if acquired:
            self._worker.drop_stale_links(acquired)

    def _initialize_channel_widget(self, slot: int, channel: int) -> None:
        try:
            limits = self._worker.fetch_channel_constraints(slot, channel)
            payload = self._worker.fetch_channel_settings(slot, channel)
            widget = self._channel_widgets.get((slot, channel))
            if widget is not None:
                widget.set_voltage_limits(
                    vset_min=limits.get("vset_min"),
                    vset_max=limits.get("vset_max"),
                    svmax_min=limits.get("svmax_min"),
                    svmax_max=limits.get("svmax_max"),
                )
                widget.apply_settings(payload)
                ref_key = widget.get_reference_key()
                parsed = self._parse_reference_key(ref_key or "None")
                if parsed is not None:
                    self._worker.set_link_rule(
                        slot,
                        channel,
                        parsed,
                        float(widget.doubleSpinBoxReferenceOffset.value()),
                        sync_ramps=False,
                    )
                else:
                    self._worker.set_link_rule(slot, channel, None, 0.0)
        except Exception as exc:
            self.append_response_log(f"ERROR: {exc}")

    def _parse_reference_key(self, key: str) -> tuple[int, int] | None:
        raw = str(key).strip()
        if not raw or raw.lower() == "none" or ":" not in raw:
            return None
        left, right = raw.split(":", 1)
        try:
            return int(left), int(right)
        except Exception:
            return None

    @QtCore.pyqtSlot(str, str)
    def _slot_resource_action(self, action: str, resource: str) -> None:
        try:
            action_l = str(action).strip().lower()
            if action_l == "acquire":
                ok = self._worker.acquire_resource(resource)
                self.append_response_log(f"acquire resource={resource} ok={ok}")
            elif action_l == "release":
                ok = self._worker.release_resource(resource)
                self.append_response_log(f"release resource={resource} ok={ok}")
            else:
                return
            self._slot_refresh_resources()
        except Exception as exc:
            self.append_response_log(f"ERROR: {exc}")

    @QtCore.pyqtSlot(int, int, str)
    def _slot_label_changed(self, slot: int, channel: int, label: str) -> None:
        try:
            self._worker.set_channel_name(slot, channel, label)
            self.append_response_log(f"label changed slot={slot} ch={channel} label={label}")
            rows = self._worker.refresh_resources_cached()
            self.on_resources_updated(rows)
            self._sync_channel_widgets_from_rows(rows)
        except Exception as exc:
            self.append_response_log(f"ERROR: {exc}")

    @QtCore.pyqtSlot(int, int, str)
    def _slot_reference_changed(self, slot: int, channel: int, reference: str) -> None:
        try:
            parsed = self._parse_reference_key(reference)
            if parsed is None:
                self._worker.set_link_rule(slot, channel, None, 0.0)
                self.append_response_log(f"reference cleared slot={slot} ch={channel}")
                return
            # Link establishment is triggered by MainWindow and handled in
            # _slot_link_rule_requested. Keep this as an informational log.
            self.append_response_log(f"reference changed slot={slot} ch={channel} reference={reference}")
        except Exception as exc:
            self.append_response_log(f"ERROR: {exc}")

    @QtCore.pyqtSlot(int, int, str, float)
    def _slot_link_rule_requested(self, slot: int, channel: int, reference: str, offset: float) -> None:
        try:
            parsed = self._parse_reference_key(reference)
            if parsed is None:
                self._worker.set_link_rule(slot, channel, None, 0.0)
                return
            ramp_updates = self._worker.set_link_rule(
                slot, channel, parsed, float(offset), sync_ramps=True
            ) or {}
            self.apply_link_ramp_values(
                [(int(slot), int(channel)), (int(parsed[0]), int(parsed[1]))],
                ramp_updates,
            )
            self.append_response_log(
                f"link established slot={slot} ch={channel} reference={reference} offset={offset:.3f}"
            )
        except Exception as exc:
            self.append_response_log(f"ERROR: {exc}")

    @QtCore.pyqtSlot(int, int, float)
    def _slot_channel_vset(self, slot: int, channel: int, value: float) -> None:
        try:
            self._worker.apply_linked_vset(slot, channel, value)
            self.append_response_log(
                f"linked vset request slot={slot} ch={channel} requested_vset={value}"
            )
            self._apply_cached_linked_widget_settings()
            payload = self._worker.refresh_channel_snapshot(slot, channel)
            self.on_channel_updated(slot, channel, payload)
        except Exception as exc:
            self.append_response_log(f"ERROR: {exc}")

    @QtCore.pyqtSlot(int, int, bool)
    def _slot_channel_power(self, slot: int, channel: int, enabled: bool) -> None:
        try:
            linked = self._worker.get_linked_channels_recursive(slot, channel)
            widget = self._channel_widgets.get((int(slot), int(channel)))
            apply_all = False
            if len(linked) > 1:
                state_text = "ON" if bool(enabled) else "OFF"
                linked_list = ", ".join(f"{s}:{c}" for s, c in sorted(linked))
                answer = QtWidgets.QMessageBox.question(
                    self,
                    "Apply Linked Power",
                    (
                        f"Channel {slot}:{channel} is linked with: {linked_list}\n\n"
                        f"Apply power {state_text} to all linked channels?"
                    ),
                    QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No,
                    QtWidgets.QMessageBox.Yes,
                )
                apply_all = answer == QtWidgets.QMessageBox.Yes
                if not apply_all:
                    # Cancel whole toggle action including initiator.
                    if widget is not None:
                        blocker = QtCore.QSignalBlocker(widget.checkBoxEnable)
                        _ = blocker
                        widget.checkBoxEnable.setChecked(not bool(enabled))
                    self.append_response_log(
                        f"power toggle canceled initiator={slot}:{channel} enabled={enabled}"
                    )
                    return
            if apply_all:
                self._worker.set_power_for_channels(linked, bool(enabled))
                self.append_response_log(
                    f"power toggle linked count={len(linked)} initiator={slot}:{channel} enabled={enabled}"
                )
            else:
                self._worker.apply_linked_power(slot, channel, bool(enabled))
                self.append_response_log(f"power toggle slot={slot} ch={channel} enabled={enabled}")
            self._apply_cached_linked_widget_settings()
        except Exception as exc:
            self.append_response_log(f"ERROR: {exc}")

    @QtCore.pyqtSlot(int, int, float)
    def _slot_rup(self, slot: int, channel: int, value: float) -> None:
        try:
            linked = self._worker.get_linked_channels_recursive(slot, channel)
            self._worker.set_param_for_channels(linked, "RUp", value)
            self.apply_link_ramp_values(list(linked), {"rup": float(value)})
            self.append_response_log(
                f"rup changed propagated count={len(linked)} initiator={slot}:{channel} value={value}"
            )
        except Exception as exc:
            self.append_response_log(f"ERROR: {exc}")

    @QtCore.pyqtSlot(int, int, float)
    def _slot_rdown(self, slot: int, channel: int, value: float) -> None:
        try:
            linked = self._worker.get_linked_channels_recursive(slot, channel)
            self._worker.set_param_for_channels(linked, "RDWn", value)
            self.apply_link_ramp_values(list(linked), {"rdown": float(value)})
            self.append_response_log(
                f"rdown changed propagated count={len(linked)} initiator={slot}:{channel} value={value}"
            )
        except Exception as exc:
            self.append_response_log(f"ERROR: {exc}")

    @QtCore.pyqtSlot(int, int, float)
    def _slot_trip(self, slot: int, channel: int, value: float) -> None:
        try:
            self._worker.set_channel_param(slot, channel, "Trip", value)
            self.append_response_log(f"trip changed slot={slot} ch={channel} value={value}")
        except Exception as exc:
            self.append_response_log(f"ERROR: {exc}")

    @QtCore.pyqtSlot(int, int, float)
    def _slot_svmax(self, slot: int, channel: int, value: float) -> None:
        try:
            self._worker.set_channel_param(slot, channel, "SVMax", value)
            self.append_response_log(f"svmax changed slot={slot} ch={channel} value={value}")
        except Exception as exc:
            self.append_response_log(f"ERROR: {exc}")

    @QtCore.pyqtSlot(int, int, float)
    def _slot_iset(self, slot: int, channel: int, value: float) -> None:
        try:
            self._worker.set_channel_param(slot, channel, "I0Set", value)
            self.append_response_log(f"iset changed slot={slot} ch={channel} value={value}")
        except Exception as exc:
            self.append_response_log(f"ERROR: {exc}")

    @QtCore.pyqtSlot(int, int, float)
    def _slot_reference_offset_changed(self, slot: int, channel: int, delta: float) -> None:
        try:
            self._worker.apply_linked_offset(slot, channel, float(delta))
            self.append_response_log(f"reference offset slot={slot} ch={channel} delta={delta}")
            self._apply_cached_linked_widget_settings()
        except Exception as exc:
            self.append_response_log(f"ERROR: {exc}")

    @QtCore.pyqtSlot(int, int, str)
    def _slot_pdown(self, slot: int, channel: int, mode: str) -> None:
        try:
            self._worker.set_channel_param(slot, channel, "PDWN", mode)
            self.append_response_log(f"pdown changed slot={slot} ch={channel} mode={mode}")
        except Exception as exc:
            self.append_response_log(f"ERROR: {exc}")
