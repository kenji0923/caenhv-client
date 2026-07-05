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
`CAENHV_CLIENT_TCP_TOKEN` set, and be running on the target host):

```python
from caenhv_client_python import set_vset, set_power, get_channel

kw = dict(host="hv-pc", port=50251, token="labsecret")   # or CAENHV_CLIENT_REMOTE / _TCP_TOKEN env
set_vset(0, 0, 5.0, **kw)          # slot 0, channel 0 -> 5.0 V (linked+safeguarded)
set_power(0, 0, True, **kw)
print(get_channel(0, 0, **kw))     # readings + settings

# set_offset, and set_param(name in {rup,rdown,iset,trip,svmax,pdown}) also available.
# Any safeguard rejection (e.g. exceeding SVMax) raises RuntimeError with the reason.
```

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
