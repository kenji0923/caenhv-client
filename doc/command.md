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

## Fire the GUI on a REMOTE host

Purpose: let labscript on one machine raise the GUI running on another machine (e.g. the HV control PC). Show/raise only — this channel can never change HV state, and remote *launch* is impossible: keep the GUI auto-started at login on its host (put the Start-menu shortcut into `shell:startup`).

On the GUI host (opt-in, disabled by default):

```sh
set CAENHV_CLIENT_TCP_PORT=50251        # enables the TCP listener
set CAENHV_CLIENT_TCP_BIND=0.0.0.0      # optional: restrict interface
set CAENHV_CLIENT_TCP_TOKEN=labsecret   # optional: shared token
```

On the calling machine:

```python
from caenhv_client_python import fire_gui
fire_gui(host="hv-pc.lab", port=50251, token="labsecret")
# or via env: CAENHV_CLIENT_REMOTE=hv-pc.lab:50251, CAENHV_CLIENT_TCP_TOKEN=labsecret
```

The GUI acknowledges accepted requests, so a wrong token or unreachable host is reported to the caller (`notify_gui` returns False; `fire_gui` raises with instructions).

## Remote HV control (through the GUI gateway)

Purpose: let labscript set HV values programmatically. The GUI is the single gateway to the devman server; remote commands are *executed by the GUI*, so every setpoint goes through its channel-link engine and safeguards (ramp/PDwn sync, SVMax/range validation, trip protection). This is the intended path for programmatic control — never a second client writing to devman directly, which would bypass those safeguards.

Control is gated on a token: it works only when `CAENHV_CLIENT_TCP_TOKEN` is set on the GUI (in addition to `CAENHV_CLIENT_TCP_PORT`). With no token, the channel stays show/raise-only.

```python
from caenhv_client_python import set_vset, set_offset, set_power, set_param, get_channel

kw = dict(host="hv-pc.lab", port=50251, token="labsecret")   # or via CAENHV_CLIENT_REMOTE + CAENHV_CLIENT_TCP_TOKEN
set_vset(0, 0, 5.0, **kw)               # linked + safeguarded
set_power(0, 0, True, **kw)             # linked groups: applied to the whole group
set_param(0, 0, "rup", 10.0, **kw)      # rup, rdown, iset, trip, svmax, pdown
values = get_channel(0, 0, **kw)        # readings + settings
```

A safeguard rejection (e.g. a target exceeding SVMax) comes back as a `RuntimeError` carrying the reason, and nothing is moved.

Quick check from a shell:

```sh
python -c "from caenhv_client_python import fire_gui; print(fire_gui())"
```
