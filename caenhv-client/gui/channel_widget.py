from __future__ import annotations

from pathlib import Path

from PyQt5 import QtCore, QtGui, QtWidgets, uic


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
        self._ramp_limit_rup = 1000.0
        self._ramp_limit_rdwn = 1000.0
        self._apply_ramp_ranges()
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
        # Bold, slightly larger readout font; set BEFORE measuring widths so
        # the fixed widths account for the actual rendered text.
        readout_font = QtGui.QFont(self.labelVmon.font())
        readout_font.setBold(True)
        readout_font.setPointSize(readout_font.pointSize() + 1)
        for label in (self.labelStatus, self.labelVmon, self.labelImon):
            label.setFont(readout_font)
        metrics = QtGui.QFontMetrics(readout_font)
        label_min_width = metrics.horizontalAdvance("CHANNELXX") + 16
        reference_width = metrics.horizontalAdvance("XX:CHANNELXX") + 20
        status_width = metrics.horizontalAdvance("ON|RUP|RDWN") + 12
        # slotX:chY sizes to content so it pairs tightly with the name field.
        self.labelResourceName.setSizePolicy(QtWidgets.QSizePolicy.Maximum, QtWidgets.QSizePolicy.Fixed)
        self.labelResourceName.setMinimumWidth(0)
        self.labelResourceName.setMaximumWidth(16777215)
        # Name field and reference selector expand together, equal widths.
        expand_min = max(label_min_width, reference_width)
        for widget in (self.lineEditLabel, self.comboBoxReference):
            policy = QtWidgets.QSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Fixed)
            policy.setHorizontalStretch(1)
            widget.setSizePolicy(policy)
            widget.setMinimumWidth(expand_min)
            widget.setMaximumWidth(16777215)
        self.labelStatus.setMinimumWidth(status_width)
        self.labelStatus.setMaximumWidth(status_width)
        # Value labels hug their content so the gap to the next group stays a
        # constant, tight layout spacing regardless of the value's magnitude.
        for label in (self.labelVmon, self.labelImon):
            label.setSizePolicy(QtWidgets.QSizePolicy.Maximum, QtWidgets.QSizePolicy.Fixed)
            label.setMinimumWidth(0)
            label.setMaximumWidth(16777215)
        self.labelStatus.setAlignment(QtCore.Qt.AlignCenter)
        self.labelVmon.setAlignment(QtCore.Qt.AlignLeft | QtCore.Qt.AlignVCenter)
        self.labelImon.setAlignment(QtCore.Qt.AlignLeft | QtCore.Qt.AlignVCenter)
        self._apply_status_style(None)

    # Labels whose widths are aligned across all channel rows so their columns
    # line up regardless of per-channel content (slotX:chY, Vmon, Imon).
    _ALIGN_LABELS = ("labelResourceName", "labelVmon", "labelImon")

    def natural_label_widths(self) -> dict[str, int]:
        """Content width each aligned label needs, independent of any width
        already forced on it (measured from the text, so it does not feed back
        on a previously applied fixed width)."""
        widths: dict[str, int] = {}
        for name in self._ALIGN_LABELS:
            label = getattr(self, name)
            fm = label.fontMetrics()
            left, _, right, _ = label.getContentsMargins()
            widths[name] = fm.horizontalAdvance(label.text()) + left + right + 6
        return widths

    def apply_aligned_label_widths(self, widths: dict[str, int]) -> None:
        """Pin each aligned label to a shared width so columns line up."""
        for name, width in widths.items():
            label = getattr(self, name)
            label.setMinimumWidth(int(width))
            label.setMaximumWidth(int(width))

    # Electrical fault bits: OVC, OVV, UNV, external/internal trip, MAXV, ILOCK.
    _STATUS_FAULT_MASK = (1 << 3) | (1 << 4) | (1 << 5) | (1 << 6) | (1 << 7) | (1 << 8) | (1 << 11)
    _STATUS_ON_BIT = 1 << 0

    def _apply_status_style(self, value: object) -> None:
        # Color the status box: red on any fault, orange when ON (powered =
        # warning), green when OFF (unpowered = likely safe).
        try:
            bits = int(value)
        except (TypeError, ValueError):
            bg, fg = "#7f8c8d", "white"  # unknown / not yet read
        else:
            if bits & self._STATUS_FAULT_MASK:
                bg, fg = "#c0392b", "white"      # red
            elif bits & self._STATUS_ON_BIT:
                bg, fg = "#e67e22", "black"       # orange
            else:
                bg, fg = "#27ae60", "white"       # green
        self.labelStatus.setStyleSheet(
            f"QLabel {{ background-color: {bg}; color: {fg}; font-weight: bold;"
            " border: 1px solid #444; padding: 1px 4px; }"
        )

    def _format_float(self, value: object, unit: str, apply_polarity: bool = True) -> str:
        try:
            num = float(value)
            if apply_polarity and self._negative_polarity:
                num = -abs(num)
            # Use the typographic minus sign (U+2212); the ASCII hyphen is
            # too small/faint next to the digits.
            return f"{num:0.2f} {unit}".replace("-", "−")
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
        self._apply_ramp_ranges()

    def set_ramp_limits(self, rup_max: float | None = None, rdwn_max: float | None = None) -> None:
        if rup_max is not None:
            self._ramp_limit_rup = abs(float(rup_max))
        if rdwn_max is not None:
            self._ramp_limit_rdwn = abs(float(rdwn_max))
        self._apply_ramp_ranges()

    def _apply_ramp_ranges(self) -> None:
        # Signed slew convention, same as Vset: the sign is the direction of
        # signed-voltage motion the parameter governs.
        rup_limit = float(getattr(self, "_ramp_limit_rup", 1000.0))
        rdwn_limit = float(getattr(self, "_ramp_limit_rdwn", 1000.0))
        if self._negative_polarity:
            self.doubleSpinBoxRup.setRange(-rup_limit, 0.0)
            self.doubleSpinBoxRdown.setRange(0.0, rdwn_limit)
        else:
            self.doubleSpinBoxRup.setRange(0.0, rup_limit)
            self.doubleSpinBoxRdown.setRange(-rdwn_limit, 0.0)

    def _signed_rup(self, value: object) -> float:
        magnitude = abs(float(value))
        return -magnitude if self._negative_polarity else magnitude

    def _signed_rdwn(self, value: object) -> float:
        magnitude = abs(float(value))
        return magnitude if self._negative_polarity else -magnitude

    def set_ramp_values(self, rup: float | None = None, rdwn: float | None = None) -> None:
        """Set ramp spinboxes, normalizing signs to this channel's polarity."""
        if rup is not None:
            blocker = QtCore.QSignalBlocker(self.doubleSpinBoxRup)
            _ = blocker
            self.doubleSpinBoxRup.setValue(self._signed_rup(rup))
        if rdwn is not None:
            blocker = QtCore.QSignalBlocker(self.doubleSpinBoxRdown)
            _ = blocker
            self.doubleSpinBoxRdown.setValue(self._signed_rdwn(rdwn))

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
        tooltip_lines = ["Reference channel — available:"]
        for ref_slot, ref_channel, ref_label in options:
            key = f"{int(ref_slot)}:{int(ref_channel)}"
            label_text = str(ref_label).strip()
            display = key if not label_text else f"{int(ref_slot)}:{label_text}"
            self.comboBoxReference.addItem(display, key)
            tooltip_lines.append(f"{key}" + (f"  ({label_text})" if label_text else ""))
        # Hover the selector to see every reference channel with its full label.
        self.comboBoxReference.setToolTip(
            "\n".join(tooltip_lines) if len(tooltip_lines) > 1 else "No reference channels available"
        )
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
            # CAEN reports IMon as an unsigned magnitude; polarity applies
            # only to voltage displays.
            self.labelImon.setText(self._format_float(payload["imon"], "uA", apply_polarity=False))
        if "status" in payload:
            self.labelStatus.setText(self._format_status(payload["status"]))
            self._apply_status_style(payload["status"])

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
            self.doubleSpinBoxRup.setValue(self._signed_rup(payload["rup"]))
        if "rdown" in payload:
            self.doubleSpinBoxRdown.setValue(self._signed_rdwn(payload["rdown"]))
        if "trip" in payload:
            self.doubleSpinBoxTrip.setValue(float(payload["trip"]))
        if "svmax" in payload:
            self.doubleSpinBoxSVmax.setValue(float(payload["svmax"]))
        if "iset" in payload and hasattr(self, "doubleSpinBoxIset"):
            self.doubleSpinBoxIset.setValue(float(payload["iset"]))
        if "pdown" in payload:
            # Only select an existing mode (case-insensitive); never add raw
            # device values (e.g. an enum index like "0") to the RAMP/KILL list.
            text = str(payload["pdown"]).strip()
            idx = self.comboBoxPdownMode.findText(text, QtCore.Qt.MatchFixedString)
            if idx >= 0:
                self.comboBoxPdownMode.setCurrentIndex(idx)
