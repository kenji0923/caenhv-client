# Overview

This is a client application for CAEN HV supplies.
It uses `caenhv-devman-client` package's client API, rather than directly using caen_libs for resource management where a single crate is shared.
In addition to basic function to send query (e.g. channel setting) to the server with resource management, this also implements a feature of channel linking.

# Features

## Channel link
By setting a ref channel and relative level to it, it coordinate requests to servers to keep the relative levels. The relative levels for each pair should not excced the set difference even during transition of actual levels according to the setting. The sequence will be the following.
1. If channels with a reference channel are requested to directly change the Vset, raise error.
2. When a set level for a channel is requested to be changed either by changing Vset, offset from the reference channel, or powered on, it scans through pairs to gather channels to be affected recursively.
3. For each pair, determine a channel to move, which make the difference of levels smaller by applying the requested change. In making a que of modifications, the found channel making the difference smaller go earlier. If the channel was off, register turning on before Vset modification. Add more modifications to the que by the same rule recursively. If duplicate modifications are queued for the same channel, raise the error. 
5. Execute the commands in the queue.
4. Update Vset values if succeeded. If failed, dispose the modification in widgets.

### Ramp synchronization
CAEN defines RUp/RDWn in magnitude space (away from / toward zero), so the parameter pair that races during a joint shift depends on board polarity. When a link is established (and when a linked rup/rdown is edited), ramps are synchronized over the whole linked group:
- Group on a single polarity: RUp is equalized across the group, RDWn is equalized across the group (a joint move runs the same named parameter on all channels).
- Group spanning both polarities: a joint shift runs RUp on one polarity against RDWn on the other, so RUp and RDWn of all channels are forced to one common value.
On link establishment the slowest (minimum) of the involved values is used.

## GUI
The GUI runs only as a standalone Qt application. It is split into three layers:
- `gui/main_window.py` (`MainWindow`): pure UI; emits request signals, exposes update slots.
- `gui/standalone_window.py` (`StandaloneMainWindow`): controller binding UI signals to worker calls, polling, dialogs, settings persistence.
- `worker/client_worker.py` (`ClientWorker`): Qt-free core holding all devman communication and the channel-link engine.

There is no labscript/BLACS integration inside this package. External systems (e.g. a BLACS tab or worker in another project) may only *fire* the GUI via the local IPC interface below — they cannot control HV settings remotely, so no logic is duplicated outside `ClientWorker`.

## Local IPC (fire/raise)
The standalone GUI listens on a `QLocalServer` (Windows: named pipe `\\.\pipe\<name>`; POSIX: Unix socket in the temp dir). Server name defaults to `caenhv-client`, overridable with the `CAENHV_CLIENT_IPC_NAME` environment variable.

- Protocol: one newline-terminated UTF-8 token per connection. Only `show` (alias `raise`) is supported; anything else is ignored and the connection is closed.
- On `show`, the window is shown, un-minimized, and raised — without taking keyboard focus from the caller.
- Single instance: a second `caenhv-client` invocation that reaches a live server forwards `show` and exits.
- Stale sockets (POSIX crash leftovers) are reclaimed with `QLocalServer.removeServer` before listening.
- Client side: `caenhv_client.communicator` provides `fire_gui()` (raise if running, else launch detached) and `notify_gui()` (raise only). It uses `QLocalSocket`, with a plain `AF_UNIX` fallback on POSIX when PyQt5 is unavailable.
