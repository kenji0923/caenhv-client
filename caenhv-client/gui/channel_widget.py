from __future__ import annotations

from pathlib import Path

from PyQt5 import QtCore, QtWidgets, uic


class ChannelWidget(QtWidgets.QWidget):
    sig_label_changed = QtCore.pyqtSignal(int, int, str)
    sig_vset_requested = QtCore.pyqtSignal(int, int, float)
    sig_reference_changed = QtCore.pyqtSignal(int, int, str)
    sig_reference_offset_changed = QtCore.pyqtSignal(int, int, float)
    sig_power_toggled = QtCore.pyqtSignal(int, int, bool)
    sig_verbose_toggled = QtCore.pyqtSignal(int, int, bool)
    sig_rup_changed = QtCore.pyqtSignal(int, int, float)
    sig_rdown_changed = QtCore.pyqtSignal(int, int, float)
    sig_trip_changed = QtCore.pyqtSignal(int, int, float)
    sig_svmax_changed = QtCore.pyqtSignal(int, int, float)
    sig_iset_changed = QtCore.pyqtSignal(int, int, float)
    sig_pdown_changed = QtCore.pyqtSignal(int, int, str)
    sig_move_up_requested = QtCore.pyqtSignal(int, int)
    sig_move_down_requested = QtCore.pyqtSignal(int, int)

    def __init__(self, slot: int, channel: int, ui_path: Path, parent: QtWidgets.QWidget | None = None) -> None:
        super().__init__(parent)
        self.slot = slot
        self.channel = channel
        uic.loadUi(str(ui_path), self)
        self.labelResourceName.setText(f"slot{self.slot}:ch{self.channel}")
        self._verbose_visible = True
        self._negative_polarity = False
        self._emit_on_arrow_step: dict[str, bool] = {
            "doubleSpinBoxVset": False,
            "doubleSpinBoxReferenceOffset": False,
            "doubleSpinBoxRup": False,
            "doubleSpinBoxRdown": False,
            "doubleSpinBoxTrip": False,
            "doubleSpinBoxSVmax": False,
            "doubleSpinBoxIset": False,
        }
        self._lock_vset_offset = False
        self._lock_per_spinbox: dict[str, bool] = {
            "doubleSpinBoxRup": False,
            "doubleSpinBoxRdown": False,
            "doubleSpinBoxTrip": False,
            "doubleSpinBoxSVmax": False,
            "doubleSpinBoxIset": False,
        }
        self._last_emitted_vset: float | None = None
        self._last_emitted_offset: float | None = None
        self._last_emitted_rup: float | None = None
        self._last_emitted_rdown: float | None = None
        self._last_emitted_trip: float | None = None
        self._last_emitted_svmax: float | None = None
        self._last_emitted_iset: float | None = None
        self._reference_key_memory: str | None = None
        self._setup_readout_widths()
        self._wire_signals()

    def _setup_readout_widths(self) -> None:
        metrics = self.labelVmon.fontMetrics()
        name_width = metrics.horizontalAdvance("slotXX:chXX") + 10
        label_min_width = metrics.horizontalAdvance("CHANNELXX") + 16
        reference_width = metrics.horizontalAdvance("XX:CHANNELXX") + 20
        status_width = metrics.horizontalAdvance("ON|RDOWN") + 12
        vmon_width = metrics.horizontalAdvance("-0000.0 V") + 10
        imon_width = metrics.horizontalAdvance("000.00 uA") + 10
        self.labelResourceName.setMinimumWidth(name_width)
        self.labelResourceName.setMaximumWidth(name_width)
        self.lineEditLabel.setMinimumWidth(label_min_width)
        self.lineEditLabel.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Fixed)
        self.comboBoxReference.setMinimumWidth(reference_width)
        self.comboBoxReference.setMaximumWidth(reference_width)
        self.comboBoxReference.setSizePolicy(QtWidgets.QSizePolicy.Fixed, QtWidgets.QSizePolicy.Fixed)
        self.labelStatus.setMinimumWidth(status_width)
        self.labelStatus.setMaximumWidth(status_width)
        self.labelVmon.setMinimumWidth(vmon_width)
        self.labelVmon.setMaximumWidth(vmon_width)
        self.labelImon.setMinimumWidth(imon_width)
        self.labelImon.setMaximumWidth(imon_width)
        self.labelStatus.setAlignment(QtCore.Qt.AlignCenter)
        self.labelVmon.setAlignment(QtCore.Qt.AlignRight | QtCore.Qt.AlignVCenter)
        self.labelImon.setAlignment(QtCore.Qt.AlignRight | QtCore.Qt.AlignVCenter)

    def _format_float(self, value: object, unit: str) -> str:
        try:
            num = float(value)
            if self._negative_polarity:
                num = -abs(num)
            return f"{num:0.2f} {unit}"
        except Exception:
            return f"{value} {unit}"

    def _format_status(self, value: object) -> str:
        if isinstance(value, int):
            labels: list[str] = []
            bit_labels = (
                (0, "ON"),
                (1, "RUP"),
                (2, "RDWN"),
                (3, "OVC"),
                (4, "OVV"),
                (5, "UNV"),
                (6, "E_TRIP"),
                (7, "MAXV"),
                (8, "I_TRIP"),
                (9, "DIS"),
                (10, "KILL"),
                (11, "ILOCK"),
            )
            for bit, label in bit_labels:
                if value & (1 << bit):
                    labels.append(label)
            if labels:
                return "|".join(labels)
            return "OFF"
        return str(value)

    def _wire_signals(self) -> None:
        self.lineEditLabel.editingFinished.connect(self._on_label_edited)
        self.doubleSpinBoxVset.editingFinished.connect(self._on_vset_edited)
        self.comboBoxReference.currentIndexChanged.connect(self._on_reference_changed)
        self.doubleSpinBoxReferenceOffset.editingFinished.connect(self._on_reference_offset_edit_finished)
        self.checkBoxEnable.toggled.connect(self._on_power_toggled)
        self.pushButtonToggleVerbose.clicked.connect(self._on_toggle_verbose)

        self.doubleSpinBoxRup.editingFinished.connect(self._on_rup_edit_finished)
        self.doubleSpinBoxRdown.editingFinished.connect(self._on_rdown_edit_finished)
        self.doubleSpinBoxTrip.editingFinished.connect(self._on_trip_edit_finished)
        self.doubleSpinBoxSVmax.editingFinished.connect(self._on_svmax_edit_finished)
        if hasattr(self, "doubleSpinBoxIset"):
            self.doubleSpinBoxIset.editingFinished.connect(self._on_iset_edit_finished)
        self.comboBoxPdownMode.currentTextChanged.connect(self._on_pdown_changed)
        self.doubleSpinBoxVset.setContextMenuPolicy(QtCore.Qt.CustomContextMenu)
        self.doubleSpinBoxVset.customContextMenuRequested.connect(
            lambda pos: self._show_spinbox_context_menu(self.doubleSpinBoxVset, pos, lock_mode="pair")
        )
        self.doubleSpinBoxVset.installEventFilter(self)
        self.doubleSpinBoxReferenceOffset.setContextMenuPolicy(QtCore.Qt.CustomContextMenu)
        self.doubleSpinBoxReferenceOffset.customContextMenuRequested.connect(
            lambda pos: self._show_spinbox_context_menu(self.doubleSpinBoxReferenceOffset, pos, lock_mode="pair")
        )
        self.doubleSpinBoxReferenceOffset.installEventFilter(self)
        self.doubleSpinBoxRup.setContextMenuPolicy(QtCore.Qt.CustomContextMenu)
        self.doubleSpinBoxRup.customContextMenuRequested.connect(
            lambda pos: self._show_spinbox_context_menu(self.doubleSpinBoxRup, pos, lock_mode="single")
        )
        self.doubleSpinBoxRup.installEventFilter(self)
        self.doubleSpinBoxRdown.setContextMenuPolicy(QtCore.Qt.CustomContextMenu)
        self.doubleSpinBoxRdown.customContextMenuRequested.connect(
            lambda pos: self._show_spinbox_context_menu(self.doubleSpinBoxRdown, pos, lock_mode="single")
        )
        self.doubleSpinBoxRdown.installEventFilter(self)
        self.doubleSpinBoxTrip.setContextMenuPolicy(QtCore.Qt.CustomContextMenu)
        self.doubleSpinBoxTrip.customContextMenuRequested.connect(
            lambda pos: self._show_spinbox_context_menu(self.doubleSpinBoxTrip, pos, lock_mode="single")
        )
        self.doubleSpinBoxTrip.installEventFilter(self)
        self.doubleSpinBoxSVmax.setContextMenuPolicy(QtCore.Qt.CustomContextMenu)
        self.doubleSpinBoxSVmax.customContextMenuRequested.connect(
            lambda pos: self._show_spinbox_context_menu(self.doubleSpinBoxSVmax, pos, lock_mode="single")
        )
        self.doubleSpinBoxSVmax.installEventFilter(self)
        if hasattr(self, "doubleSpinBoxIset"):
            self.doubleSpinBoxIset.setContextMenuPolicy(QtCore.Qt.CustomContextMenu)
            self.doubleSpinBoxIset.customContextMenuRequested.connect(
                lambda pos: self._show_spinbox_context_menu(self.doubleSpinBoxIset, pos, lock_mode="single")
            )
            self.doubleSpinBoxIset.installEventFilter(self)
        if hasattr(self, "toolButtonMoveUp"):
            self.toolButtonMoveUp.clicked.connect(
                lambda: self.sig_move_up_requested.emit(self.slot, self.channel)
            )
        if hasattr(self, "toolButtonMoveDown"):
            self.toolButtonMoveDown.clicked.connect(
                lambda: self.sig_move_down_requested.emit(self.slot, self.channel)
            )

    def _show_spinbox_context_menu(
        self,
        spinbox: QtWidgets.QDoubleSpinBox,
        pos: QtCore.QPoint,
        *,
        lock_mode: str,
    ) -> None:
        line_edit = spinbox.lineEdit()
        if line_edit is not None:
            menu = line_edit.createStandardContextMenu()
        else:
            menu = QtWidgets.QMenu(self)
        menu.addSeparator()
        lock_action = menu.addAction("Lock This Field" if lock_mode == "single" else "Lock Vset/Offset")
        lock_action.setCheckable(True)
        key = str(spinbox.objectName())
        if lock_mode == "single":
            lock_action.setChecked(bool(self._lock_per_spinbox.get(key, False)))
        else:
            lock_action.setChecked(bool(self._lock_vset_offset))
        toggle_action = menu.addAction("Send On Arrow Step")
        toggle_action.setCheckable(True)
        toggle_action.setChecked(bool(self._emit_on_arrow_step.get(key, False)))
        action = menu.addAction("Set Step...")
        selected = menu.exec_(spinbox.mapToGlobal(pos))
        if selected == lock_action:
            if lock_mode == "single":
                self._set_single_spinbox_locked(spinbox, bool(lock_action.isChecked()))
            else:
                self._set_vset_offset_locked(bool(lock_action.isChecked()))
            return
        if selected == toggle_action:
            self._emit_on_arrow_step[key] = bool(toggle_action.isChecked())
            return
        if selected != action:
            return
        current_step = float(spinbox.singleStep())
        value, ok = QtWidgets.QInputDialog.getDouble(
            self,
            "Set Step",
            "Step size:",
            current_step,
            0.0,
            1.0e9,
            6,
        )
        if not ok:
            return
        step = float(value)
        if step <= 0.0:
            return
        spinbox.setSingleStep(step)

    def _set_vset_offset_locked(self, locked: bool) -> None:
        self._lock_vset_offset = bool(locked)
        for spinbox in (self.doubleSpinBoxVset, self.doubleSpinBoxReferenceOffset):
            spinbox.setReadOnly(self._lock_vset_offset)
            spinbox.setButtonSymbols(
                QtWidgets.QAbstractSpinBox.NoButtons
                if self._lock_vset_offset
                else QtWidgets.QAbstractSpinBox.UpDownArrows
            )

    def _set_single_spinbox_locked(self, spinbox: QtWidgets.QDoubleSpinBox, locked: bool) -> None:
        key = str(spinbox.objectName())
        self._lock_per_spinbox[key] = bool(locked)
        spinbox.setReadOnly(bool(locked))
        spinbox.setButtonSymbols(
            QtWidgets.QAbstractSpinBox.NoButtons
            if bool(locked)
            else QtWidgets.QAbstractSpinBox.UpDownArrows
        )

    def _on_label_edited(self) -> None:
        self.sig_label_changed.emit(self.slot, self.channel, self.lineEditLabel.text())

    def _on_vset_edited(self) -> None:
        value = float(self.doubleSpinBoxVset.value())
        if self._last_emitted_vset is not None and abs(self._last_emitted_vset - value) <= 1e-12:
            return
        self._last_emitted_vset = value
        self.sig_vset_requested.emit(self.slot, self.channel, value)

    def _on_reference_changed(self, _index: int) -> None:
        data = self.comboBoxReference.currentData()
        key = "None" if data is None else str(data)
        self._reference_key_memory = None if key.strip().lower() == "none" else key.strip()
        self.sig_reference_changed.emit(self.slot, self.channel, key)

    def _on_reference_offset_edit_finished(self) -> None:
        value = float(self.doubleSpinBoxReferenceOffset.value())
        if self._last_emitted_offset is not None and abs(self._last_emitted_offset - value) <= 1e-12:
            return
        self._last_emitted_offset = value
        self.sig_reference_offset_changed.emit(self.slot, self.channel, value)

    def eventFilter(self, watched: QtCore.QObject, event: QtCore.QEvent) -> bool:
        if isinstance(watched, QtWidgets.QDoubleSpinBox) and event.type() == QtCore.QEvent.KeyPress:
            key_name = str(watched.objectName())
            if self._emit_on_arrow_step.get(key_name, False):
                key_event = event  # type: ignore[assignment]
                if key_event.key() in (QtCore.Qt.Key_Up, QtCore.Qt.Key_Down):
                    if watched is self.doubleSpinBoxVset:
                        QtCore.QTimer.singleShot(0, self._on_vset_edited)
                    elif watched is self.doubleSpinBoxReferenceOffset:
                        QtCore.QTimer.singleShot(0, self._on_reference_offset_edit_finished)
                    elif watched is self.doubleSpinBoxRup:
                        QtCore.QTimer.singleShot(0, self._on_rup_edit_finished)
                    elif watched is self.doubleSpinBoxRdown:
                        QtCore.QTimer.singleShot(0, self._on_rdown_edit_finished)
                    elif watched is self.doubleSpinBoxTrip:
                        QtCore.QTimer.singleShot(0, self._on_trip_edit_finished)
                    elif watched is self.doubleSpinBoxSVmax:
                        QtCore.QTimer.singleShot(0, self._on_svmax_edit_finished)
                    elif hasattr(self, "doubleSpinBoxIset") and watched is self.doubleSpinBoxIset:
                        QtCore.QTimer.singleShot(0, self._on_iset_edit_finished)
        return super().eventFilter(watched, event)

    def _on_power_toggled(self, enabled: bool) -> None:
        self.sig_power_toggled.emit(self.slot, self.channel, bool(enabled))

    def _on_toggle_verbose(self) -> None:
        self.set_verbose_visible(not self._verbose_visible)
        self.sig_verbose_toggled.emit(self.slot, self.channel, self._verbose_visible)

    def set_verbose_visible(self, visible: bool) -> None:
        self._verbose_visible = bool(visible)
        self.widgetVerboseConfig.setVisible(self._verbose_visible)
        self.pushButtonToggleVerbose.setText("Hide" if self._verbose_visible else "Show")

    def is_verbose_visible(self) -> bool:
        return bool(self._verbose_visible)

    def set_negative_polarity(self, negative: bool) -> None:
        self._negative_polarity = bool(negative)
        # Use real signed values in the input widgets instead of visual prefix tricks.
        self.doubleSpinBoxVset.setPrefix("")
        self.doubleSpinBoxSVmax.setPrefix("")
        if self._negative_polarity:
            self.doubleSpinBoxVset.setMaximum(0.0)
            self.doubleSpinBoxSVmax.setMaximum(0.0)
        else:
            self.doubleSpinBoxVset.setMaximum(3000.0)
            self.doubleSpinBoxSVmax.setMaximum(3000.0)

    def set_voltage_limits(self, *, vset_min: float | None = None, vset_max: float | None = None, svmax_min: float | None = None, svmax_max: float | None = None) -> None:
        if vset_min is not None:
            self.doubleSpinBoxVset.setMinimum(float(vset_min))
        if vset_max is not None:
            self.doubleSpinBoxVset.setMaximum(float(vset_max))
        if svmax_min is not None:
            self.doubleSpinBoxSVmax.setMinimum(float(svmax_min))
        if svmax_max is not None:
            self.doubleSpinBoxSVmax.setMaximum(float(svmax_max))

    def set_reference_options(self, options: list[tuple[int, int, str]]) -> None:
        current_key = self.get_reference_key()
        blocker = QtCore.QSignalBlocker(self.comboBoxReference)
        _ = blocker
        self.comboBoxReference.clear()
        self.comboBoxReference.addItem("None", None)
        for ref_slot, ref_channel, ref_label in options:
            key = f"{int(ref_slot)}:{int(ref_channel)}"
            label_text = str(ref_label).strip()
            display = key if not label_text else f"{int(ref_slot)}:{label_text}"
            self.comboBoxReference.addItem(display, key)
        self.set_reference_key(current_key)

    def set_reference_key(self, key: str | None) -> None:
        self._reference_key_memory = None if key is None or str(key).strip().lower() == "none" else str(key).strip()
        if key is None or str(key).strip().lower() == "none":
            self.comboBoxReference.setCurrentIndex(0)
            return
        target = str(key).strip()
        for i in range(self.comboBoxReference.count()):
            if str(self.comboBoxReference.itemData(i) or "") == target:
                self.comboBoxReference.setCurrentIndex(i)
                return
        # Keep the memorized reference even if options are temporarily missing.
        self.comboBoxReference.setCurrentIndex(0)

    def get_reference_key(self) -> str | None:
        if self._reference_key_memory is not None:
            text = str(self._reference_key_memory).strip()
            return text or None
        data = self.comboBoxReference.currentData()
        if data is None:
            return None
        text = str(data).strip()
        return text or None

    def set_reference_offset(self, value: float) -> None:
        blocker = QtCore.QSignalBlocker(self.doubleSpinBoxReferenceOffset)
        _ = blocker
        self.doubleSpinBoxReferenceOffset.setValue(float(value))
        self._last_emitted_offset = float(value)

    def _on_rup_edit_finished(self) -> None:
        value = float(self.doubleSpinBoxRup.value())
        if self._last_emitted_rup is not None and abs(self._last_emitted_rup - value) <= 1e-12:
            return
        self._last_emitted_rup = value
        self.sig_rup_changed.emit(self.slot, self.channel, value)

    def _on_rdown_edit_finished(self) -> None:
        value = float(self.doubleSpinBoxRdown.value())
        if self._last_emitted_rdown is not None and abs(self._last_emitted_rdown - value) <= 1e-12:
            return
        self._last_emitted_rdown = value
        self.sig_rdown_changed.emit(self.slot, self.channel, value)

    def _on_trip_edit_finished(self) -> None:
        value = float(self.doubleSpinBoxTrip.value())
        if self._last_emitted_trip is not None and abs(self._last_emitted_trip - value) <= 1e-12:
            return
        self._last_emitted_trip = value
        self.sig_trip_changed.emit(self.slot, self.channel, value)

    def _on_svmax_edit_finished(self) -> None:
        value = float(self.doubleSpinBoxSVmax.value())
        if self._last_emitted_svmax is not None and abs(self._last_emitted_svmax - value) <= 1e-12:
            return
        self._last_emitted_svmax = value
        self.sig_svmax_changed.emit(self.slot, self.channel, value)

    def _on_iset_edit_finished(self) -> None:
        if not hasattr(self, "doubleSpinBoxIset"):
            return
        value = float(self.doubleSpinBoxIset.value())
        if self._last_emitted_iset is not None and abs(self._last_emitted_iset - value) <= 1e-12:
            return
        self._last_emitted_iset = value
        self.sig_iset_changed.emit(self.slot, self.channel, value)

    def _on_pdown_changed(self, text: str) -> None:
        self.sig_pdown_changed.emit(self.slot, self.channel, text)

    def update_display(self, payload: dict) -> None:
        if "vmon" in payload:
            self.labelVmon.setText(self._format_float(payload["vmon"], "V"))
        if "imon" in payload:
            self.labelImon.setText(self._format_float(payload["imon"], "uA"))
        if "status" in payload:
            self.labelStatus.setText(self._format_status(payload["status"]))

    def apply_settings(self, payload: dict) -> None:
        blockers = [
            QtCore.QSignalBlocker(self.lineEditLabel),
            QtCore.QSignalBlocker(self.doubleSpinBoxVset),
            QtCore.QSignalBlocker(self.checkBoxEnable),
            QtCore.QSignalBlocker(self.doubleSpinBoxRup),
            QtCore.QSignalBlocker(self.doubleSpinBoxRdown),
            QtCore.QSignalBlocker(self.doubleSpinBoxTrip),
            QtCore.QSignalBlocker(self.doubleSpinBoxSVmax),
            QtCore.QSignalBlocker(self.comboBoxPdownMode),
        ]
        if hasattr(self, "doubleSpinBoxIset"):
            blockers.append(QtCore.QSignalBlocker(self.doubleSpinBoxIset))
        _ = blockers
        if "label" in payload:
            self.lineEditLabel.setText(str(payload["label"]))
        if "vset" in payload:
            self.doubleSpinBoxVset.setValue(float(payload["vset"]))
        if "power" in payload:
            value = payload["power"]
            enabled = bool(int(value)) if isinstance(value, (int, float, str)) else bool(value)
            self.checkBoxEnable.setChecked(enabled)
        if "rup" in payload:
            self.doubleSpinBoxRup.setValue(float(payload["rup"]))
        if "rdown" in payload:
            self.doubleSpinBoxRdown.setValue(float(payload["rdown"]))
        if "trip" in payload:
            self.doubleSpinBoxTrip.setValue(float(payload["trip"]))
        if "svmax" in payload:
            self.doubleSpinBoxSVmax.setValue(float(payload["svmax"]))
        if "iset" in payload and hasattr(self, "doubleSpinBoxIset"):
            self.doubleSpinBoxIset.setValue(float(payload["iset"]))
        if "pdown" in payload:
            text = str(payload["pdown"])
            if self.comboBoxPdownMode.findText(text) < 0:
                self.comboBoxPdownMode.addItem(text)
            self.comboBoxPdownMode.setCurrentText(text)
