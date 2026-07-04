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

Configuration via environment variables:

- `CAENHV_CLIENT_COMMAND` — path (or command line) of the caenhv-client
  executable, used when it is not on `PATH`.
- `CAENHV_CLIENT_IPC_NAME` — IPC server name (default `caenhv-client`),
  must match the GUI's setting when overridden.

Works from any Python process, including labscript BLACS tabs and workers;
no Qt required (a Unix-socket / named-pipe transport is used, with PyQt5 as
an optional fallback).
