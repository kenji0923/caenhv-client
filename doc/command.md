# Commands

## Run the GUI

Purpose: operate CAEN HV supplies through the devman server with the standalone Qt GUI.

```sh
caenhv-client        # console script
caenhv-client-gui    # GUI script (no console window on Windows)
python -m caenhv_client
```

If an instance is already running, a second invocation raises the existing window (without taking keyboard focus) and exits immediately.

The IPC server name defaults to `caenhv-client`; set the `CAENHV_CLIENT_IPC_NAME` environment variable (in both the GUI and the caller) to use a different name, e.g. to run independent instances.

## Fire the GUI from another project (e.g. a BLACS tab or worker)

Purpose: let external code bring up the caenhv-client GUI without duplicating any HV logic. This is the only remote capability — there is no remote control of HV settings.

```python
from caenhv_client.communicator import fire_gui, notify_gui

fire_gui()      # raise the window if running, otherwise launch the GUI detached
notify_gui()    # raise only; returns False if the GUI is not running
```

Requirements in the calling environment: `caenhv-client` installed. On Linux no PyQt5 is needed (a plain Unix-socket fallback is used); on Windows PyQt5 must be importable (BLACS installs always have it).

Quick check from a shell:

```sh
python -c "from caenhv_client.communicator import fire_gui; print(fire_gui())"
```
