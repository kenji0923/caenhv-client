# caenhv-client-python

Python interface to the [caenhv-client](https://github.com/kenji0923/caenhv-client)
GUI (CAEN HV control with channel linking). Zero dependencies; the GUI itself
is distributed as a standalone executable.

Two capabilities:

1. *Fire* the GUI — raise its window if running, launch it otherwise.
2. *Control HV through the GUI* — the GUI is the single gateway to the
   devman server, so remote setpoints are executed by it and pass through
   its channel-link engine and safeguards (ramp/PDwn sync, SVMax
   validation, trip protection). Control requires a token configured on the
   GUI; without one the channel is show/raise-only.

```python
from caenhv_client_python import fire_gui, notify_gui

fire_gui()      # raise the window if running, otherwise launch the GUI
notify_gui()    # raise only; returns False if the GUI is not running
```

Remote HV control (the GUI must have `CAENHV_CLIENT_TCP_PORT` and a
`CAENHV_CLIENT_TCP_TOKEN` set, and be running on the target host).

Recommended — a bound client, set host/port/token once:

```python
from caenhv_client_python import RemoteClient

hv = RemoteClient("hv-pc", 50251, token="labsecret")   # or RemoteClient.from_env()
hv.set_vset(0, 0, 5.0)          # slot 0, channel 0 -> 5.0 V (linked + safeguarded)
hv.set_power(0, 0, True)
print(hv.get_channel(0, 0))     # readings + settings
hv.set_param(0, 0, "rup", 10.0) # rup, rdown, iset, trip, svmax, pdown
hv.raise_window()               # bring the GUI forward (no focus steal)
```

Typed getters mirror the setters (each returns one value, raising a clear
error if the parameter could not be read):

```python
hv.get_vset(0, 0)          # float, volts (signed by polarity)
hv.get_vmon(0, 0)          # float, measured voltage (signed)
hv.get_imon(0, 0)          # float, measured current
hv.get_power(0, 0)         # bool
hv.get_status(0, 0)        # int, CAEN Status bitmask
hv.get_param(0, 0, "svmax")  # rup, rdown, iset, trip, svmax, pdown
```

Channel-link relationship and offset:

```python
hv.get_link(0, 1)          # {'linked': True, 'master_slot': 0, 'master_channel': 0, 'offset': -200.0}
hv.get_offset(0, 1)        # -200.0
hv.set_offset(0, 1, -150.0)  # change the relative level (linked + safeguarded)
```

Atomic multi-channel apply — set several linked vset/offset changes at once so
a valid final state is not rejected at an intermediate per-channel step:

```python
hv.set_linked_bulk([
    {"slot": 0, "ch": 0, "vset": 1000.0},    # a master setpoint
    {"slot": 0, "ch": 1, "offset": 2000.0},  # a linked channel's relative level
])   # -> {'status': 'ok', 'targets': {'0:0': 1000.0, '0:1': 3000.0}}
```

Bulk read — many channels in one round-trip (the low-overhead path):

```python
rows = hv.get_many([(0, 0), (0, 1), (1, 0)], include_link=True)
# rows[i] = {'vmon':.., 'vset':.., 'imon':.., 'power':.., 'status':..,
#            'link': {'linked':.., 'master_slot':.., 'master_channel':.., 'offset':..}}
# a channel that cannot be read appears as {'error': '...'} at its index
```

For many calls, hold one connection open (reconnects automatically on error):

```python
hv = RemoteClient("hv-pc", 50251, token="labsecret", persistent=True)
try:
    while running:
        rows = hv.get_many(channels, include_link=True)
finally:
    hv.close()          # or use `with RemoteClient(...) as hv:`
```

For several fields at once, `get_channel(0, 0)` returns them all in one
round-trip: keys `vset, vmon, imon, power, status, rup, rdown, iset, trip,
svmax, pdown, label` (a key is absent if that parameter could not be read).

The module-level functions (`set_vset`, `set_power`, `get_channel`,
`set_offset`, `set_param`, `send_command`) remain available if you prefer
passing `host=`, `port=`, `token=` per call. Any safeguard rejection (e.g.
exceeding SVMax) raises `RuntimeError` with the reason, and nothing moves.

Remote hosts: with `fire_gui(host="hv-pc", port=50251)` (or
`CAENHV_CLIENT_REMOTE=hv-pc:50251`) the show request goes over TCP to a GUI
on another machine. The GUI must enable its listener
(`CAENHV_CLIENT_TCP_PORT=50251`, optional `CAENHV_CLIENT_TCP_BIND` /
`CAENHV_CLIENT_TCP_TOKEN`) and must already be running there — remote
launch is impossible, so auto-start the GUI at login on that host. This
channel can only show/raise the window, never change HV state.

Configuration via environment variables:

- `CAENHV_CLIENT_COMMAND` — path (or command line) of the caenhv-client
  executable, used when it is not on `PATH`.
- `CAENHV_CLIENT_IPC_NAME` — IPC server name (default `caenhv-client`),
  must match the GUI's setting when overridden.
- `CAENHV_CLIENT_REMOTE` — `host:port` of a remote GUI's TCP listener.
- `CAENHV_CLIENT_TCP_TOKEN` — shared token when the remote listener
  requires one.

Works from any Python process, including labscript BLACS tabs and workers;
no Qt required (a Unix-socket / named-pipe transport is used, with PyQt5 as
an optional fallback).
