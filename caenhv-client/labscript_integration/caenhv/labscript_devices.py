"""CAEN HV labscript device definition."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from labscript import Device, StaticAnalogOut, StaticDigitalOut, config
from labscript.utils import LabscriptError


@dataclass(frozen=True)
class ChannelSpec:
    slot: int
    channel: int


class CAENHV(Device):
    """Static CAEN HV device for BLACS/manual control via devman bridge."""

    description = "CAEN HV (devman)"
    allowed_children = [StaticAnalogOut, StaticDigitalOut]

    def __init__(
        self,
        name: str,
        *,
        server_host: str = "127.0.0.1",
        server_port: int = 50250,
        client_name: str | None = None,
        channels: list[tuple[int, int]] | None = None,
        bridge_search_paths: list[str] | None = None,
    ) -> None:
        self.BLACS_connection = f"{server_host}:{int(server_port)}"
        self._channels = [ChannelSpec(int(s), int(c)) for s, c in (channels or [])]
        self._analog_outputs: dict[str, StaticAnalogOut] = {}
        self._digital_outputs: dict[str, StaticDigitalOut] = {}

        added_properties = {
            "server_host": str(server_host),
            "server_port": int(server_port),
            "client_name": str(client_name) if client_name else f"blacs_{name}",
            "channels": [(ch.slot, ch.channel) for ch in self._channels],
            "bridge_search_paths": list(bridge_search_paths or []),
        }
        Device.__init__(
            self,
            name=name,
            parent_device=None,
            connection=None,
            added_properties=added_properties,
        )
        self._create_default_channels()

    def _conn(self, slot: int, channel: int, field: str) -> str:
        return f"slot{int(slot)}_ch{int(channel)}_{field}"

    def _add_analog(self, slot: int, channel: int, field: str) -> None:
        connection = self._conn(slot, channel, field)
        output = StaticAnalogOut(f"{self.name}_{connection}", self, connection)
        self._analog_outputs[connection] = output

    def _add_digital(self, slot: int, channel: int, field: str) -> None:
        connection = self._conn(slot, channel, field)
        output = StaticDigitalOut(f"{self.name}_{connection}", self, connection)
        self._digital_outputs[connection] = output

    def _create_default_channels(self) -> None:
        for ch in self._channels:
            self._add_analog(ch.slot, ch.channel, "vset")
            self._add_analog(ch.slot, ch.channel, "iset")
            self._add_analog(ch.slot, ch.channel, "rup")
            self._add_analog(ch.slot, ch.channel, "rdown")
            self._add_analog(ch.slot, ch.channel, "trip")
            self._add_analog(ch.slot, ch.channel, "svmax")
            self._add_digital(ch.slot, ch.channel, "enable")

    def generate_code(self, hdf5_file) -> None:
        Device.generate_code(self, hdf5_file)

        if self.parent_device is None:
            for child in self.child_devices:
                if isinstance(child, StaticAnalogOut):
                    child.expand_timeseries()

        ao_table = self._make_analog_out_table()
        do_table = self._make_digital_out_table()
        output = self._merge_output_tables(ao_table, do_table)

        grp = self.init_device_group(hdf5_file)
        grp.create_dataset("output", data=output, compression=config.compression)

    def _make_analog_out_table(self) -> np.ndarray:
        if not self._analog_outputs:
            return np.empty(1, dtype=[])
        connections = sorted(self._analog_outputs)
        dtypes = [(c, np.float64) for c in connections]
        table = np.empty(1, dtype=dtypes)
        for connection, output in self._analog_outputs.items():
            table[connection] = output.raw_output
        return table

    def _make_digital_out_table(self) -> np.ndarray:
        if not self._digital_outputs:
            return np.empty(1, dtype=[])
        connections = sorted(self._digital_outputs)
        dtypes = [(c, bool) for c in connections]
        table = np.empty(1, dtype=dtypes)
        for connection, output in self._digital_outputs.items():
            value = output.static_value
            if not isinstance(value, (bool, np.bool_, int, np.integer)):
                raise LabscriptError(
                    f"Unsupported static digital value type for {connection}: {type(value)}"
                )
            table[connection] = bool(value)
        return table

    def _merge_output_tables(self, ao_table: np.ndarray, do_table: np.ndarray) -> np.ndarray:
        dtype = ao_table.dtype.descr + do_table.dtype.descr
        output = np.empty(ao_table.shape, dtype=dtype)
        for name in ao_table.dtype.names or []:
            output[name] = ao_table[name]
        for name in do_table.dtype.names or []:
            output[name] = do_table[name]
        return output

