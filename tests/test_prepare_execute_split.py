"""Tests for the prepare/execute split of linked setpoint applies.

prepare_* validates and adopts the intent (fast, no crate I/O);
execute_prepared_plan runs the crate writes and reverts the adoption on
failure. read_channel_brief serves vset from the adopted intent. Hardware
and validation boundaries are stubbed per instance; no devman needed.

Run:  ~/.pyenv/daq/bin/python -m pytest tests/test_prepare_execute_split.py -q
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

_WORKER_PATH = Path(__file__).resolve().parents[1] / "caenhv-client" / "worker" / "client_worker.py"
_spec = importlib.util.spec_from_file_location("client_worker_under_test", _WORKER_PATH)
client_worker = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = client_worker
_spec.loader.exec_module(_spec and client_worker)

ClientWorker = client_worker.ClientWorker


def make_worker(**channel_states):
    """Worker with stubbed validation/hardware boundaries.

    channel_states: {'s1c2': {'vset': ..., 'power': ...}} keyed as s<slot>c<ch>.
    """
    worker = ClientWorker()
    for key, state in channel_states.items():
        slot, channel = key[1:].split("c")
        worker._channel_state[(int(slot), int(channel))] = dict(state)
    worker.calls = []
    worker._validate_vset_targets_in_range = lambda targets: worker.calls.append(
        ("validate_range", dict(targets))
    )
    worker._validate_linked_power_consistency = (
        lambda *, initiator, affected: worker.calls.append(("validate_power", initiator))
    )
    worker._ensure_group_ramps_synced = lambda channels: None
    worker._sync_link_pdown = lambda channels, *, adopt_from, strict: None
    worker._group_ramping_channels = lambda channels: []
    def _record_execute(targets, pre_vsets=None):
        worker.calls.append(("execute", dict(targets), dict(pre_vsets or {})))
    worker._execute_vset_plan = _record_execute
    return worker


def test_prepare_adopts_intent_and_records_previous():
    worker = make_worker(s1c2={"vset": 1.0, "power": True})
    plan = worker.prepare_linked_vset(1, 2, 5.0)
    assert worker._channel_state[(1, 2)]["vset"] == 5.0  # intent adopted
    assert plan["pre_vsets"] == {(1, 2): 1.0}
    assert plan["targets"] == {(1, 2): 5.0}
    assert ("validate_range", {(1, 2): 5.0}) in worker.calls
    assert ("execute", {(1, 2): 5.0}, {(1, 2): 1.0}) not in worker.calls  # no hw yet


def test_prepare_validation_failure_restores_rules_and_intent():
    worker = make_worker(s1c2={"vset": 1.0, "power": True},
                         s1c3={"vset": 3.0, "power": True})
    worker._link_rules[(1, 2)] = ((1, 3), -2.0)  # ch2 linked to ch3

    def _reject(targets):
        raise RuntimeError("SVMax exceeded")

    worker._validate_vset_targets_in_range = _reject
    with pytest.raises(RuntimeError, match="SVMax"):
        worker.prepare_linked_vset(1, 2, 100.0)
    assert worker._link_rules[(1, 2)] == ((1, 3), -2.0)  # rule restored
    assert worker._channel_state[(1, 2)]["vset"] == 1.0  # intent untouched


def test_execute_uses_true_pre_vsets_for_ordering():
    worker = make_worker(s1c2={"vset": 1.0, "power": True})
    plan = worker.prepare_linked_vset(1, 2, 5.0)
    result = worker.execute_prepared_plan(plan)
    # execution received the pre-adoption vsets, not the adopted intent
    assert ("execute", {(1, 2): 5.0}, {(1, 2): 1.0}) in worker.calls
    assert result["targets"] == {"1:2": 5.0}


def test_execute_failure_drops_intent_and_restores_rules():
    worker = make_worker(s1c2={"vset": 1.0, "power": True},
                         s1c3={"vset": 3.0, "power": True})
    worker._link_rules[(1, 2)] = ((1, 3), -2.0)
    plan = worker.prepare_linked_vset(1, 2, 5.0)
    assert worker._channel_state[(1, 2)]["vset"] == 5.0

    def _fail(targets, pre_vsets=None):
        raise RuntimeError("V0Set write failed")

    worker._execute_vset_plan = _fail
    with pytest.raises(RuntimeError, match="V0Set write failed"):
        worker.execute_prepared_plan(plan)
    assert worker._link_rules[(1, 2)] == ((1, 3), -2.0)  # rule restored
    # adopted intent dropped: the next read refetches hardware truth
    for key in plan["targets"]:
        assert key not in worker._channel_state


def test_apply_linked_vset_is_prepare_plus_execute():
    worker = make_worker(s1c2={"vset": 1.0, "power": True})
    result = worker.apply_linked_vset(1, 2, 7.5)
    assert ("execute", {(1, 2): 7.5}, {(1, 2): 1.0}) in worker.calls
    assert result["targets"] == {"1:2": 7.5}
    assert worker._channel_state[(1, 2)]["vset"] == 7.5


def test_prepare_linked_bulk_adopts_all_targets():
    worker = make_worker(s1c2={"vset": 1.0, "power": True},
                         s1c3={"vset": 3.0, "power": True})
    plan = worker.prepare_linked_bulk([
        {"slot": 1, "ch": 2, "vset": 10.0},
        {"slot": 1, "ch": 3, "vset": 20.0},
    ])
    assert plan["targets"] == {(1, 2): 10.0, (1, 3): 20.0}
    assert worker._channel_state[(1, 2)]["vset"] == 10.0
    assert worker._channel_state[(1, 3)]["vset"] == 20.0
    assert plan["pre_vsets"] == {(1, 2): 1.0, (1, 3): 3.0}


class BridgeStub:
    """Records get_ch_param calls; serves Pw only (vset must not be read)."""

    def __init__(self):
        self.calls = []

    def Device_get_ch_param(self, slot, channels, name):
        self.calls.append((slot, tuple(channels), name))
        if name in ("Pw", "PW", "Pon"):
            return [1]
        raise AssertionError(f"unexpected crate read of {name}")


def test_read_channel_brief_serves_vset_from_intent():
    worker = make_worker(s1c2={"vset": 4.25, "power": True})
    bridge = BridgeStub()
    worker._ensure_bridge = lambda **kwargs: bridge
    worker.refresh_channel_snapshot = lambda slot, channel: {"vmon": 4.24, "status": 1}
    payload = worker.read_channel_brief(1, 2)
    assert payload["vset"] == 4.25  # intent, not a crate read
    assert all(name != "V0Set" for (_, _, name) in bridge.calls)


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
