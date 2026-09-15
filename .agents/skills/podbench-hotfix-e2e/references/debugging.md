# Debugger preparation and recovery

These lessons come from the 2026-09-14 T11 walkthrough. Check current code and
live state before applying a workaround; a workaround is not a product fix.

## Existing holds and stale Attach ownership

Record source diffs, hold tokens, the generated launcher's `.hold` and `.session`
records, and the actual process identity before attaching. A held application
can still be healthy. A missing standalone debugpy process does not prove that
no debugger is active: the adapter can be injected into the application.

If Attach reports `this Attach configuration already owns a session`, compare
its saved PID and start time with `/proc` and inspect active debugger sessions.
Do not remove an ownership file or release someone else's hold just to retry.
For a proven dead owner, preserve the diagnostic evidence and release only its
identified token through the supported lifecycle API. Inspect the current API
rather than guessing command flags. Do not clear the entire holds directory.

The tested `ide_prepare.py` exception path unlinked `.hold` even when exclusive
creation failed because that file already existed. Thus one failed attempt can
change ownership state. Recheck it before retrying and report this as a product
finding. Never use repeated Attach attempts as a stale-session cleanup method.

## Identify the actual application

The supervised child can be a wrapper. On mo-sim it was `stdio-socket`, with the
native IOC beneath it. Select the generated launcher for the actual executable;
do not assume the supervised PID is the debugger target. Record both identities.

After saving the workspace or launch settings, recheck the selected launcher.
VS Code can switch to a repository-provided configuration, such as
`(gdb) Attach to IOC`, which opens a different process picker. Cancel that picker
and select the intended Podbench launcher before retrying.

## Native attach and source breakpoints

On the tested mo-sim setup, cppdbg failed with an out-of-range `arch` argument.
GDB identified the executable as `i386:x86-64`. Adding
`"targetArchitecture": "x64"` to that run's generated configuration through the
editor allowed attachment. Verify the actual architecture first and preserve
settings for restoration. Record that the generated configuration needed a
workaround; do not generalize x64 to ARM workloads.

If a gutter breakpoint stays unbound, inspect the loaded library's debug
information and source paths, the generated `sourceFileMap`, and the actual file
VS Code opened. A function breakpoint can reveal the debugger's source path.
Then place a source-line breakpoint through the UI. In the walkthrough, a
VS Code function-breakpoint entry `motorSimDriver.cpp:400` hit the next executable
line, 401, in optimized code. Record both requested and resolved lines. A hollow
gutter marker is not proof; require a paused-on-breakpoint stack and source.

The source displayed through `/proc/PID/root/epics/...` may be image source,
not the writable claim. For a native edit, prepare a separate module copy under
a run-specific claim directory, verify an unchanged build first, then edit that
copy through VS Code and rebuild. Prove the restarted process loads the rebuilt
library (for example, inspect its mapped library path) and executes the changed
code. Merely compiling or editing a startup echo does not prove a native fix.
Preserve existing startup edits and restore any temporary library-path override.

## Native disconnect is an acceptance boundary

The tested generated native Attach configuration used `request: "launch"` and
custom GDB attach commands. Pressing Shift+F5 (Stop) killed the attached IOC:
previous-container logs showed `Killed`, exit 137, and the application container
restart count increased from 0 to 1. The pod recovered, but the existing seat
terminated too. This is a failed lifecycle assertion, not successful cleanup.

Before repeating native debugging, establish a non-terminating detach route in
the actual adapter/UI and inspect the current generated configuration. Do not
use the red Stop button or Shift+F5 as a substitute for Disconnect. If the UI
provides Disconnect, verify its behaviour against the adapter's capabilities;
that route was not validated in this walkthrough. If a safe route cannot be
established, report native debugging blocked before attaching. Fixing or
validating the product's detach behaviour is a separate task from repeating the
demo. Do not claim a settings workaround fixes the observed termination bug.

Immediately after any debugger exit, check pod UID, application restart count,
child/process identity, readiness and holds. If the container restarted, retain
previous logs and status before they disappear, mark the assertion failed, and
restore health. A later fresh baseline cannot erase that failure. Reconnect only
if continuing remains in scope; a terminated ephemeral seat may require a new
one. Ephemeral containers cannot be removed individually: report retained seats
without rolling the workload merely to remove them.

## Restore precisely and stop cleanly

Save the exact pre-run source content/diff, not just Git HEAD. A single Undo may
undo only part of a text insertion; verify the full restored line and diff
before restarting. Use per-command `git -c safe.directory=/podbench/app` for a
known shared claim if Git rejects its ownership, rather than weakening global
Git configuration.

If the user pauses the walkthrough, cancel new demo work, tell the worker to
finish only necessary cleanup, and record a resume point. Keep completed stages
and screenshots, failed assertions, unrun stages, final health, pre-existing
edits preserved, and resources retained. Never finish an unrequested edit after
the user has asked to leave the test for another day.

## Native detach after podbench PR #270

The generated gdb script now defines `hook-kill` to detach, so the C++
extension's `kill` on Stop (Shift+F5) ends with "The program is not being
run." and the application keeps running; validated twice on P47 mo-ioc. VS Code
offers no separate Disconnect for this launch-type session. Still check the
restart count and holds after every session end.

## Attach exits with code 42 and a silent console

Seen on P47 mo-ioc. Causes: the claim repository's own `.vscode/launch.json`
configuration was selected instead of the Podbench launcher; a hidden native
dialog on the run's display blocked input (raise or dismiss it with xdotool on
that display only). Adding `"targetArchitecture": "x64"` and engine logging to
the generated configuration through the editor made the failure visible.
A function breakpoint on a poll routine re-hits every cycle; remove it before
continuing, and a breakpoint on a probed health endpoint is hit by the kubelet
every ~10 s.
