# Commands

## Run the GUI

Purpose: operate CAEN HV supplies through the devman server with the standalone Qt GUI.

Deployment (Windows lab machines): download `caenhv-client.exe` from GitHub Releases (built by the `build-windows` workflow on `v*` tags) and run it — no Python environment needed.

From source / pip install (development):

```sh
caenhv-client        # console script
caenhv-client-gui    # GUI script (no console window on Windows)
python -m caenhv_client
```

If an instance is already running, a second invocation raises the existing window (without taking keyboard focus) and exits immediately.

The IPC server name defaults to `caenhv-client`; set the `CAENHV_CLIENT_IPC_NAME` environment variable (in both the GUI and the caller) to use a different name, e.g. to run independent instances.

## Start-menu shortcut (Windows)

Purpose: create (or remove) a Start-menu shortcut that launches the GUI with the application icon and no console window.

```sh
caenhv-client.exe --install-shortcut     # PyInstaller distribution
caenhv-client.exe --remove-shortcut

caenhv-client-install-shortcut           # pip/source installs
caenhv-client-install-shortcut --remove
```

Windows only; on other platforms the command exits with a message.

## Fire the GUI from another project (e.g. a BLACS tab or worker)

Purpose: let external code bring up the caenhv-client GUI without duplicating any HV logic. This is the only remote capability — there is no remote control of HV settings.

```sh
pip install caenhv-client-python   # thin, zero-dependency (tag py-v* publishes it)
```

```python
from caenhv_client_python import fire_gui, notify_gui

fire_gui()      # raise the window if running, otherwise launch the GUI detached
notify_gui()    # raise only; returns False if the GUI is not running
```

If the GUI executable is not on PATH, set `CAENHV_CLIENT_COMMAND` to its full path (or a command line). No PyQt5 is required on any platform (Unix-socket / named-pipe transport; QLocalSocket is only an optional fallback). Inside this repo, `caenhv_client.communicator` is a shim re-exporting the same functions.

Quick check from a shell:

```sh
python -c "from caenhv_client_python import fire_gui; print(fire_gui())"
```
