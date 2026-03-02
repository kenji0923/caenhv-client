from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from PyQt5 import QtCore, QtWidgets, uic

try:
    from .channel_widget import ChannelWidget
except Exception:
    from channel_widget import ChannelWidget


class MainWindow(QtWidgets.QWidget):
    """UI/controller base class.

    This class owns GUI state and Qt signal/slot wiring only.
    It does not call backend/client worker methods directly.
    """

    sig_connect_requested = QtCore.pyqtSignal(str, int, str, bool)
    sig_disconnect_requested = QtCore.pyqtSignal()
    sig_refresh_resources_requested = QtCore.pyqtSignal()
    sig_save_status_requested = QtCore.pyqtSignal()
    sig_load_status_requested = QtCore.pyqtSignal()
    sig_resource_action_requested = QtCore.pyqtSignal(str, str)

    sig_channel_vset_requested = QtCore.pyqtSignal(int, int, float)
    sig_channel_power_toggled = QtCore.pyqtSignal(int, int, bool)
    sig_reference_changed = QtCore.pyqtSignal(int, int, str)
    sig_reference_offset_changed = QtCore.pyqtSignal(int, int, float)
    sig_link_rule_requested = QtCore.pyqtSignal(int, int, str, float)
    sig_label_changed = QtCore.pyqtSignal(int, int, str)
    sig_rup_changed = QtCore.pyqtSignal(int, int, float)
    sig_rdown_changed = QtCore.pyqtSignal(int, int, float)
    sig_trip_changed = QtCore.pyqtSignal(int, int, float)
    sig_svmax_changed = QtCore.pyqtSignal(int, int, float)
    sig_iset_changed = QtCore.pyqtSignal(int, int, float)
    sig_pdown_changed = QtCore.pyqtSignal(int, int, str)

    def __init__(self, root_dir: Path, parent: QtWidgets.QWidget | None = None) -> None:
        super().__init__(parent)
        self._root_dir = root_dir
        candidates = [
            root_dir / "caenhv-client" / "gui" / "ui",
            root_dir / "caenhv_client" / "gui" / "ui",
            Path(__file__).resolve().parent / "ui",
        ]
        ui_dir = next((p for p in candidates if (p / "main.ui").exists()), candidates[-1])
        self._main_ui = ui_dir / "main.ui"
        self._channel_ui = ui_dir / "channel.ui"
        uic.loadUi(str(self._main_ui), self)

        self._channel_widgets: dict[tuple[int, int], ChannelWidget] = {}
        self._connected = False
        self._ui_settings = self._create_settings("caenhv_client_ui")
        self._active_client_name = ""
        self._saved_channel_order: list[str] = []
        self._saved_verbose_by_channel: dict[str, bool] = {}
        self._saved_reference_by_channel: dict[str, dict[str, Any]] = {}
        self._wire_main_signals()
        self._configure_splitter_behavior()
        self._sync_section_visibility()
        self.set_connected(False)

    def _wire_main_signals(self) -> None:
        self.pushButtonConnect.clicked.connect(self.on_connect_clicked)
        self.pushButtonDisconnect.clicked.connect(self.on_disconnect_clicked)
        self.pushButtonChannelSettingUiExpandAll.clicked.connect(self.expand_all_channels)
        self.pushButtonChannelSettingUiCollapseAll.clicked.connect(self.collapse_all_channels)

        self.groupBoxConnection.toggled.connect(lambda v: self.on_section_toggled("connection", v))
        self.groupBoxResource.toggled.connect(lambda v: self.on_section_toggled("resource", v))
        self.groupBoxResponse.toggled.connect(lambda v: self.on_section_toggled("response", v))
        self.groupBoxChannelSetting.toggled.connect(lambda v: self.on_section_toggled("channel", v))

        self.treeWidgetResources.itemClicked.connect(self.on_resource_item_clicked)
        if hasattr(self, "pushButtonResourceRefresh"):
            self.pushButtonResourceRefresh.clicked.connect(self.on_refresh_resources_clicked)
        if hasattr(self, "pushButtonSaveStatus"):
            self.pushButtonSaveStatus.clicked.connect(self.on_save_status_clicked)
        if hasattr(self, "pushButtonLoadStatus"):
            self.pushButtonLoadStatus.clicked.connect(self.on_load_status_clicked)

    def _configure_splitter_behavior(self) -> None:
        splitter = getattr(self, "splitterMainSections", None)
        if splitter is None:
            return
        splitter.setChildrenCollapsible(False)
        for i in range(splitter.count()):
            splitter.setCollapsible(i, False)
            widget = splitter.widget(i)
            if widget is None:
                continue
            preferred_height = max(
                widget.sizeHint().height(),
                widget.minimumSizeHint().height(),
                max(20, widget.fontMetrics().height() + 12),
            )
            widget.setMinimumHeight(preferred_height)
            widget.setSizePolicy(QtWidgets.QSizePolicy.Preferred, QtWidgets.QSizePolicy.Preferred)

    def _create_settings(self, basename: str) -> QtCore.QSettings:
        return QtCore.QSettings(
            QtCore.QSettings.IniFormat,
            QtCore.QSettings.UserScope,
            "caenhv_client",
            basename,
        )

    def _channel_key(self, slot: int, channel: int) -> str:
        return f"{int(slot)}:{int(channel)}"

    def _parse_reference_key(self, key: str) -> tuple[int, int] | None:
        raw = str(key).strip()
        if not raw or raw.lower() == "none" or ":" not in raw:
            return None
        left, right = raw.split(":", 1)
        try:
            return int(left), int(right)
        except Exception:
            return None

    def _current_client_name(self) -> str:
        if str(self._active_client_name).strip():
            return str(self._active_client_name).strip()
        return self.lineEditClientName.text().strip()

    def _settings_scope(self) -> str:
        client_name = self._current_client_name() or "_default"
        return f"channel_ui/{client_name}"

    def _layout_channel_widgets(self) -> list[ChannelWidget]:
        widgets: list[ChannelWidget] = []
        for i in range(self.layoutChannelSetting.count()):
            item = self.layoutChannelSetting.itemAt(i)
            if item is None:
                continue
            widget = item.widget()
            if isinstance(widget, ChannelWidget):
                widgets.append(widget)
        return widgets

    def _save_channel_ui_state(self) -> None:
        scope = self._settings_scope()
        current_order = [self._channel_key(w.slot, w.channel) for w in self._layout_channel_widgets()]
        order = list(current_order)
        for key in self._saved_channel_order:
            if key not in order:
                order.append(str(key))

        verbose = dict(self._saved_verbose_by_channel)
        verbose.update(
            {
            self._channel_key(w.slot, w.channel): bool(w.is_verbose_visible())
            for w in self._channel_widgets.values()
            }
        )

        references = dict(self._saved_reference_by_channel)
        references.update(
            {
            self._channel_key(w.slot, w.channel): {
                "reference": w.get_reference_key(),
                "offset": float(w.doubleSpinBoxReferenceOffset.value()),
            }
            for w in self._channel_widgets.values()
            }
        )
        self._saved_channel_order = list(order)
        self._saved_verbose_by_channel = dict(verbose)
        self._saved_reference_by_channel = dict(references)
        self._ui_settings.setValue(f"{scope}/order", order)
        self._ui_settings.setValue(f"{scope}/verbose", json.dumps(verbose))
        self._ui_settings.setValue(f"{scope}/references", json.dumps(references))

    def _load_channel_ui_state(self) -> None:
        scope = self._settings_scope()
        raw_order = self._ui_settings.value(f"{scope}/order", [])
        if isinstance(raw_order, str):
            raw_order = [raw_order] if raw_order else []
        if isinstance(raw_order, (tuple, list)):
            self._saved_channel_order = [str(x) for x in raw_order if str(x).strip()]
        else:
            self._saved_channel_order = []

        raw_verbose = self._ui_settings.value(f"{scope}/verbose", "{}")
        parsed: dict[str, Any] = {}
        if isinstance(raw_verbose, str):
            try:
                obj = json.loads(raw_verbose)
                if isinstance(obj, dict):
                    parsed = obj
            except Exception:
                parsed = {}
        elif isinstance(raw_verbose, dict):
            parsed = raw_verbose
        self._saved_verbose_by_channel = {str(k): bool(v) for k, v in parsed.items()}

        raw_references = self._ui_settings.value(f"{scope}/references", "{}")
        parsed_refs: dict[str, Any] = {}
        if isinstance(raw_references, str):
            try:
                obj = json.loads(raw_references)
                if isinstance(obj, dict):
                    parsed_refs = obj
            except Exception:
                parsed_refs = {}
        elif isinstance(raw_references, dict):
            parsed_refs = raw_references
        cleaned_refs: dict[str, dict[str, Any]] = {}
        for key, value in parsed_refs.items():
            if not isinstance(value, dict):
                continue
            cleaned_refs[str(key)] = {
                "reference": value.get("reference"),
                "offset": float(value.get("offset", 0.0)),
            }
        self._saved_reference_by_channel = cleaned_refs

    def _apply_saved_channel_order(self) -> None:
        if not self._channel_widgets:
            return
        desired = list(self._saved_channel_order)
        existing = set(self._channel_key(s, c) for (s, c) in self._channel_widgets)
        placed: set[str] = set()
        index = 0
        for key in desired:
            if key in placed or key not in existing:
                continue
            try:
                slot_s, channel_s = key.split(":", 1)
                slot_i = int(slot_s)
                channel_i = int(channel_s)
            except Exception:
                continue
            widget = self._channel_widgets.get((slot_i, channel_i))
            if widget is None:
                continue
            self.layoutChannelSetting.removeWidget(widget)
            self.layoutChannelSetting.insertWidget(index, widget)
            placed.add(key)
            index += 1
        for slot, channel in sorted(self._channel_widgets):
            key = self._channel_key(slot, channel)
            if key in placed:
                continue
            widget = self._channel_widgets[(slot, channel)]
            self.layoutChannelSetting.removeWidget(widget)
            self.layoutChannelSetting.insertWidget(index, widget)
            index += 1

    def _apply_saved_verbose_state(self) -> None:
        for (slot, channel), widget in self._channel_widgets.items():
            key = self._channel_key(slot, channel)
            visible = self._saved_verbose_by_channel.get(key)
            if visible is not None:
                widget.set_verbose_visible(bool(visible))

    def _apply_saved_reference_state(self) -> None:
        for (slot, channel), widget in self._channel_widgets.items():
            key = self._channel_key(slot, channel)
            saved = self._saved_reference_by_channel.get(key)
            if not isinstance(saved, dict):
                continue
            ref_blocker = QtCore.QSignalBlocker(widget.comboBoxReference)
            off_blocker = QtCore.QSignalBlocker(widget.doubleSpinBoxReferenceOffset)
            _ = (ref_blocker, off_blocker)
            widget.set_reference_key(saved.get("reference"))
            try:
                widget.set_reference_offset(float(saved.get("offset", 0.0)))
            except Exception:
                widget.set_reference_offset(0.0)

    def _sync_section_visibility(self) -> None:
        self._apply_group_box_state(self.groupBoxConnection, self.groupBoxConnection.isChecked())
        self._apply_group_box_state(self.groupBoxResource, self.groupBoxResource.isChecked())
        self._apply_group_box_state(self.groupBoxResponse, self.groupBoxResponse.isChecked())
        self._apply_group_box_state(self.groupBoxChannelSetting, self.groupBoxChannelSetting.isChecked())

    def _group_box_for_section(self, section: str) -> QtWidgets.QGroupBox | None:
        if section == "connection":
            return self.groupBoxConnection
        if section == "resource":
            return self.groupBoxResource
        if section == "response":
            return self.groupBoxResponse
        if section == "channel":
            return self.groupBoxChannelSetting
        return None

    def _set_layout_item_visible(self, item: QtWidgets.QLayoutItem, visible: bool) -> None:
        widget = item.widget()
        if widget is not None:
            widget.setVisible(bool(visible))
            return

        layout = item.layout()
        if layout is not None:
            for i in range(layout.count()):
                self._set_layout_item_visible(layout.itemAt(i), visible)

    def _set_group_content_visible(self, group_box: QtWidgets.QGroupBox, visible: bool) -> None:
        layout = group_box.layout()
        if layout is None:
            return
        for i in range(layout.count()):
            self._set_layout_item_visible(layout.itemAt(i), visible)

    def _apply_group_box_state(self, group_box: QtWidgets.QGroupBox, enabled: bool) -> None:
        self._set_group_content_visible(group_box, bool(enabled))
        if enabled:
            group_box.setMinimumHeight(0)
            group_box.setMaximumHeight(16777215)
            return

        # Keep only the group-box title row visible when collapsed.
        header_height = max(20, group_box.fontMetrics().height() + 12)
        group_box.setMinimumHeight(header_height)
        group_box.setMaximumHeight(header_height)

    @QtCore.pyqtSlot()
    def on_connect_clicked(self) -> None:
        host = self.lineEditServerHost.text().strip() or "127.0.0.1"
        client_name = self.lineEditClientName.text().strip()
        if not client_name:
            self.append_response_log("ERROR: client name is required")
            return
        force = bool(getattr(self, "checkBoxForceConnect", None) and self.checkBoxForceConnect.isChecked())
        self.sig_connect_requested.emit(host, 50250, client_name, force)

    @QtCore.pyqtSlot()
    def on_disconnect_clicked(self) -> None:
        self.sig_disconnect_requested.emit()

    @QtCore.pyqtSlot()
    def on_refresh_resources_clicked(self) -> None:
        self.sig_refresh_resources_requested.emit()

    @QtCore.pyqtSlot()
    def on_save_status_clicked(self) -> None:
        self.sig_save_status_requested.emit()

    @QtCore.pyqtSlot()
    def on_load_status_clicked(self) -> None:
        self.sig_load_status_requested.emit()

    @QtCore.pyqtSlot(QtWidgets.QTreeWidgetItem, int)
    def on_resource_item_clicked(self, item: QtWidgets.QTreeWidgetItem, column: int) -> None:
        if column != 3:
            return
        row = item.data(0, QtCore.Qt.UserRole)
        if not isinstance(row, dict):
            return

        action = str(row.get("action") or "").strip().lower()
        resource = str(row.get("resource") or "").strip()
        if action not in ("acquire", "release") or not resource:
            return

        self.sig_resource_action_requested.emit(action, resource)

    def on_section_toggled(self, section: str, enabled: bool) -> None:
        group_box = self._group_box_for_section(section)
        if group_box is not None:
            self._apply_group_box_state(group_box, bool(enabled))
        self.append_response_log(f"section toggled section={section} enabled={enabled}")

    def append_response_log(self, message: str) -> None:
        self.textBrowserResponse.append(message)

    def set_connected(self, connected: bool) -> None:
        self._connected = bool(connected)
        self.pushButtonConnect.setEnabled(not self._connected)
        self.pushButtonDisconnect.setEnabled(self._connected)
        self.lineEditServerHost.setEnabled(not self._connected)
        self.lineEditClientName.setEnabled(not self._connected)
        if hasattr(self, "checkBoxForceConnect"):
            self.checkBoxForceConnect.setEnabled(not self._connected)

    @QtCore.pyqtSlot(dict)
    def on_connected(self, payload: dict[str, Any]) -> None:
        self._active_client_name = str(payload.get("client_name") or "").strip()
        self._load_channel_ui_state()
        self.set_connected(True)
        self.append_response_log(f"connected: {payload}")

    @QtCore.pyqtSlot()
    def on_disconnected(self) -> None:
        self._save_channel_ui_state()
        self.set_connected(False)
        self.append_response_log("disconnected")

    def expand_all_channels(self) -> None:
        for widget in self._channel_widgets.values():
            widget.set_verbose_visible(True)
        self._save_channel_ui_state()

    def collapse_all_channels(self) -> None:
        for widget in self._channel_widgets.values():
            widget.set_verbose_visible(False)
        self._save_channel_ui_state()

    def _prune_channel_widgets(self, keep: set[tuple[int, int]]) -> None:
        removed = False
        for key in list(self._channel_widgets.keys()):
            if key in keep:
                continue
            widget = self._channel_widgets.pop(key)
            self.layoutChannelSetting.removeWidget(widget)
            widget.deleteLater()
            removed = True
        if removed:
            self._save_channel_ui_state()

    @QtCore.pyqtSlot(int, int, bool)
    def _on_channel_verbose_toggled(self, _slot: int, _channel: int, _visible: bool) -> None:
        self._save_channel_ui_state()

    @QtCore.pyqtSlot(int, int, str)
    def _on_channel_reference_changed(self, _slot: int, _channel: int, _reference: str) -> None:
        self._save_channel_ui_state()

    @QtCore.pyqtSlot(int, int, float)
    def _on_channel_reference_offset_changed(self, _slot: int, _channel: int, _delta: float) -> None:
        self._save_channel_ui_state()

    @QtCore.pyqtSlot(int, int, str)
    def _on_channel_reference_selected(self, slot: int, channel: int, reference: str) -> None:
        widget = self._channel_widgets.get((int(slot), int(channel)))
        if widget is None:
            return
        parsed = self._parse_reference_key(reference)
        if parsed is None:
            self.sig_link_rule_requested.emit(int(slot), int(channel), "None", 0.0)
            return
        ref_widget = self._channel_widgets.get((int(parsed[0]), int(parsed[1])))
        current_vset = float(widget.doubleSpinBoxVset.value())
        if ref_widget is None:
            offset = float(widget.doubleSpinBoxReferenceOffset.value())
        else:
            offset = current_vset - float(ref_widget.doubleSpinBoxVset.value())
            widget.set_reference_offset(offset)
        self.sig_link_rule_requested.emit(int(slot), int(channel), str(reference), float(offset))

    @QtCore.pyqtSlot(int, int)
    def _on_channel_move_up(self, slot: int, channel: int) -> None:
        widget = self._channel_widgets.get((int(slot), int(channel)))
        if widget is None:
            return
        index = self.layoutChannelSetting.indexOf(widget)
        if index <= 0:
            return
        self.layoutChannelSetting.removeWidget(widget)
        self.layoutChannelSetting.insertWidget(index - 1, widget)
        self._save_channel_ui_state()

    @QtCore.pyqtSlot(int, int)
    def _on_channel_move_down(self, slot: int, channel: int) -> None:
        widget = self._channel_widgets.get((int(slot), int(channel)))
        if widget is None:
            return
        widgets = self._layout_channel_widgets()
        if not widgets:
            return
        try:
            idx = widgets.index(widget)
        except ValueError:
            return
        if idx >= len(widgets) - 1:
            return
        self.layoutChannelSetting.removeWidget(widget)
        self.layoutChannelSetting.insertWidget(idx + 1, widget)
        self._save_channel_ui_state()

    def _update_reference_selectors(self, rows: list[dict[str, Any]]) -> None:
        acquired: dict[tuple[int, int], str] = {}
        for row in rows:
            if str(row.get("action") or "").strip().lower() != "release":
                continue
            channel = row.get("channel")
            if channel is None:
                continue
            slot = int(row.get("slot"))
            ch = int(channel)
            acquired[(slot, ch)] = str(row.get("channel_label") or f"{ch}")

        for (slot, ch), widget in self._channel_widgets.items():
            options: list[tuple[int, int, str]] = []
            for (ref_slot, ref_ch), ref_label in sorted(acquired.items()):
                if ref_slot == slot and ref_ch == ch:
                    continue
                options.append((ref_slot, ref_ch, ref_label))
            widget.set_reference_options(options)

    @QtCore.pyqtSlot(list)
    def on_resources_updated(self, rows: list[dict[str, Any]]) -> None:
        self.treeWidgetResources.clear()
        acquired_keys: set[tuple[int, int]] = set()

        slots: dict[int, dict[str, Any]] = {}
        for row in rows:
            slot = int(row.get("slot", -1))
            if slot < 0:
                continue
            entry = slots.setdefault(
                slot,
                {
                    "board": str(row.get("board") or "Board"),
                    "slot_row": None,
                    "channels": [],
                },
            )
            if row.get("channel") is None:
                entry["slot_row"] = row
            else:
                entry["channels"].append(row)

        for slot in sorted(slots):
            entry = slots[slot]
            slot_row = entry.get("slot_row") or {
                "slot": slot,
                "board": entry["board"],
                "channel": None,
                "owner": "",
                "action": "",
                "resource": f"slot:{slot}",
            }

            top = QtWidgets.QTreeWidgetItem(
                [f"{slot}: {entry['board']}", "", str(slot_row.get("owner") or ""), str(slot_row.get("action") or "")]
            )
            top.setData(0, QtCore.Qt.UserRole, slot_row)
            self.treeWidgetResources.addTopLevelItem(top)

            channel_rows = sorted(
                entry["channels"],
                key=lambda r: int(r.get("channel", -1)),
            )
            for row in channel_rows:
                channel = int(row.get("channel", -1))
                channel_label = str(row.get("channel_label") or channel)
                child = QtWidgets.QTreeWidgetItem(
                    ["", channel_label, str(row.get("owner") or ""), str(row.get("action") or "")]
                )
                child.setData(0, QtCore.Qt.UserRole, row)
                top.addChild(child)
                if str(row.get("action") or "").strip().lower() == "release":
                    acquired_keys.add((slot, channel))
                    widget = self.ensure_channel_widget(slot, channel)
                    widget.set_negative_polarity(bool(row.get("negative_polarity")))

        self._prune_channel_widgets(acquired_keys)
        self._apply_saved_channel_order()
        self._apply_saved_verbose_state()
        self._update_reference_selectors(rows)
        self._apply_saved_reference_state()

        self.treeWidgetResources.expandAll()
        self._save_channel_ui_state()
        self.append_response_log(f"resources updated count={len(rows)}")

    @QtCore.pyqtSlot(int, int, dict)
    def on_channel_updated(self, slot: int, channel: int, payload: dict[str, Any]) -> None:
        key = (slot, channel)
        widget = self._channel_widgets.get(key)
        if widget is not None:
            widget.update_display(payload)

    def ensure_channel_widget(self, slot: int, channel: int) -> ChannelWidget:
        key = (slot, channel)
        if key in self._channel_widgets:
            return self._channel_widgets[key]

        widget = ChannelWidget(slot, channel, self._channel_ui, self)
        self.layoutChannelSetting.addWidget(widget)
        self._wire_channel_widget(widget)
        self._channel_widgets[key] = widget
        saved_visible = self._saved_verbose_by_channel.get(self._channel_key(slot, channel))
        if saved_visible is not None:
            widget.set_verbose_visible(bool(saved_visible))
        return widget

    def apply_link_ramp_values(
        self,
        channels: list[tuple[int, int]],
        ramp_updates: dict[str, float],
    ) -> None:
        rup_value = ramp_updates.get("rup")
        rdown_value = ramp_updates.get("rdown")
        if rup_value is None and rdown_value is None:
            return
        for slot, channel in channels:
            widget = self._channel_widgets.get((int(slot), int(channel)))
            if widget is None:
                continue
            if rup_value is not None:
                blocker_rup = QtCore.QSignalBlocker(widget.doubleSpinBoxRup)
                _ = blocker_rup
                widget.doubleSpinBoxRup.setValue(float(rup_value))
            if rdown_value is not None:
                blocker_rdown = QtCore.QSignalBlocker(widget.doubleSpinBoxRdown)
                _ = blocker_rdown
                widget.doubleSpinBoxRdown.setValue(float(rdown_value))

    def _wire_channel_widget(self, widget: ChannelWidget) -> None:
        widget.sig_vset_requested.connect(self.sig_channel_vset_requested)
        widget.sig_power_toggled.connect(self.sig_channel_power_toggled)
        widget.sig_reference_changed.connect(self.sig_reference_changed)
        widget.sig_reference_offset_changed.connect(self.sig_reference_offset_changed)
        widget.sig_reference_changed.connect(self._on_channel_reference_changed)
        widget.sig_reference_changed.connect(self._on_channel_reference_selected)
        widget.sig_reference_offset_changed.connect(self._on_channel_reference_offset_changed)
        widget.sig_label_changed.connect(self.sig_label_changed)
        widget.sig_rup_changed.connect(self.sig_rup_changed)
        widget.sig_rdown_changed.connect(self.sig_rdown_changed)
        widget.sig_trip_changed.connect(self.sig_trip_changed)
        widget.sig_svmax_changed.connect(self.sig_svmax_changed)
        widget.sig_iset_changed.connect(self.sig_iset_changed)
        widget.sig_pdown_changed.connect(self.sig_pdown_changed)
        widget.sig_verbose_toggled.connect(self._on_channel_verbose_toggled)
        widget.sig_move_up_requested.connect(self._on_channel_move_up)
        widget.sig_move_down_requested.connect(self._on_channel_move_down)

    def closeEvent(self, event) -> None:
        self._save_channel_ui_state()
        super().closeEvent(event)
