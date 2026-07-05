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

### Signed slew convention (GUI, logging)
RUp/RDwn are displayed, edited, and logged as signed slew rates: the sign is the direction of signed-voltage motion the parameter governs (positive board: RUp > 0, RDwn < 0; negative board: RUp < 0, RDwn > 0), handled like Vset — signed everywhere in the UI/worker API, magnitude only at the CAEN write/read boundary. Under this convention the sync rule is uniform for all groups: all positive-signed slews equal, all negative-signed slews equal. Note this deviates from CAEN's own magnitude convention (GECO, manuals).

### Ramp synchronization
CAEN defines RUp/RDWn in magnitude space (away from / toward zero), so the parameter pair that races during a joint shift depends on board polarity. When a link is established (and when a linked rup/rdown is edited), ramps are synchronized over the whole linked group:
- Group on a single polarity: RUp is equalized across the group, RDWn is equalized across the group (a joint move runs the same named parameter on all channels).
- Group spanning both polarities: a joint shift runs RUp on one polarity against RDWn on the other, so RUp and RDWn of all channels are forced to one common value.
On link establishment the slowest (minimum) of the involved values is used. Link creation fails (and the link is not kept) if any ramp value cannot be read or written.

### Link safeguards
To keep the voltage difference of every linked pair within its set difference during transitions:
- Before any linked Vset/offset/power request, ramp values of the whole group are read back; if they drifted (e.g. changed by another client), the group is automatically re-synced to the slowest value and a warning is logged.
- If a V0Set write fails mid-queue, already-moved channels are rolled back (best-effort) to their previous set values so the group does not settle at a wrong difference.
- PDWN is kept synchronized across linked channels: the mode is adopted from the reference at link establishment, edits propagate to the group, and a drifted group is re-synced (to the initiator's mode) before a linked power-off.
- If a linked channel trips (Status shows external/internal trip), the remaining ON partners are powered off automatically and a warning is logged.
- Link groups are mirrored to the devman server registry (`set_link_groups`) on every link change; the server-side trip watchdog (see caenhv-devman) then protects the groups even while this GUI is closed. Unsupported/unreachable registries only produce a log note.
- Registry staleness: on every connect the client re-pushes its restored link state (possibly empty), replacing whatever a previous session left under the same client name. In addition the registry works as a lease: the GUI re-pushes its groups every ~30 s while connected, and the server janitors groups whose owner lease expired — removing them only when all member channels are cleanly off, keeping (and still watchdog-protecting) them whenever any member is energized. A wrongly janitored group self-heals at the next renewal. Groups persist only (a) while the client is disconnected (intended — that is what the watchdog protects), or (b) under an abandoned client *name*, which the GUI surfaces at connect time with a NOTE listing groups registered by other clients (clear them by connecting once under that name with no links). Resource strings are slot/channel based, so re-slotting boards invalidates registered groups the same way it invalidates ownership records.
- Hardware trip lines (TripInt/TripExt) are programmed automatically on link changes where the boards support them: each group gets one trip line (internal board line for same-board groups, one of the crate's 4 external lines for cross-board groups, which mixed-polarity groups always are), with sense+propagate bits set on every member so partners trip together in hardware. Lines are cleared and freed when a group is dropped. Capability gaps, exhausted lines, or write failures fall back to the software watchdog and are reported in the log.
- Releasing a resource drops all link rules involving its channels (client side), clears their reference selections in the UI, and re-pushes the registry — links to not-owned channels do not linger.
- Vset targets are validated against SVMax in addition to the parameter range, so hardware clamping cannot silently change the achieved difference.
- A warning is logged when a linked move is issued while group channels are still ramping.

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
- Client side: the `caenhv-client-python` package (re-exported by `caenhv_client.communicator`) provides `fire_gui()` (raise if running, else launch detached) and `notify_gui()` (raise only), using stdlib Unix-socket / named-pipe transports with QLocalSocket as optional fallback.
- Remote hosts: an opt-in TCP listener (`CAENHV_CLIENT_TCP_PORT`, with bind-address and shared-token options) accepts the same one-line protocol from other machines, replying `ok` on acceptance. Show/raise only, never HV control; remote launch is impossible by design — the GUI is expected to auto-start at login on its host.
