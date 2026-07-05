# caenhv-client-python

Python interface to the [caenhv-client](https://github.com/kenji0923/caenhv-client)
GUI (CAEN HV control with channel linking). Zero dependencies; the GUI itself
is distributed as a standalone executable.

The only capability is *firing* the GUI — raise its window if running,
launch it otherwise — over a tiny local IPC protocol. There is deliberately
no remote control of HV settings.

```python
from caenhv_client_python import fire_gui, notify_gui

fire_gui()      # raise the window if running, otherwise launch the GUI
notify_gui()    # raise only; returns False if the GUI is not running
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
