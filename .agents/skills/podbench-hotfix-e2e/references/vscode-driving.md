# Driving the actual VS Code UI

Use an available desktop automation tool, or a headless VS Code instance whose
workbench can be controlled and captured. Inspect the chosen driver's usage
before running it. If no UI driver is available, explain how to supply one and
report the IDE stages blocked; a direct debugger protocol client is not a UI
substitute.

For environments with the claude-sandbox checkout, its
`.claude/skills/vscode-headless` skill provides `scripts/vscode-headless` and
`scripts/vscode-ui.mjs`. Find the actual checkout rather than assuming a path.
Read its setup instructions; on Linux these may require Xvfb and browser/runtime
libraries. Set `VSCODE_HEADLESS_SKILL` to the skill directory when using the
existing helper. Launch with:

```sh
podbench ide vscode POD -n NAMESPACE --target NAME \
    --code /actual/path/to/vscode-headless
```

Give each run a fresh user-data directory and unused DevTools port. Podbench
opens a bootstrap window first. Select the workspace window matching the
selected target and wait for its `[SSH: podbench...]` suffix and completed
connection, even after the CLI prints `Ready`. Verify the actual remote folder
and process; the title alone does not prove the correct target.

Drive editor navigation, breakpoint placement, Run and Debug, variable/stack
inspection, edits and saving through the UI. Use snapshots to locate controls
and screenshots to verify visual state. On a fresh profile the chat box may
hold focus: explicitly focus the editor before typing, and open the integrated
terminal through the command palette when needed. Do not count text echoed
from a typed terminal command as output evidence.

Verify whether the integrated terminal is local or remote before executing
commands; a remote workspace can still open a local terminal. Check its host
and paths. Use explicit local kubeconfig for workstation kubectl, or commands
available in the verified seat. Python may attempt to activate a remote venv
inside a local terminal: cancel stray input and clear irrelevant errors before
capturing evidence. Keep the actual command and fresh output visible.

After selecting a debug panel or hidden control, verify focus before typing an
expression. If text lands in the editor accidentally, undo it before saving and
verify the source content. Prefer locating visible controls over blind keys.

If input appears unresponsive but the workbench snapshot looks normal, check
for a native dialog on the dedicated virtual display. Electron dialogs can sit
behind the workbench and be absent from its DOM snapshot and window screenshot.
Inspect the virtual display's window tree, raise the relevant transient dialog,
and handle it before retrying the failed action. Keep this inspection confined
to this run's display. Capture a full-display diagnostic if the workbench-only
image hides the problem. For example, with the run's actual display and window ID:

```sh
DISPLAY=:109 xwininfo -root -tree
DISPLAY=:109 xdotool windowraise WINDOW_ID windowfocus WINDOW_ID
```

Capture the full virtual display with an available X11 screenshot tool (the
walkthrough used Pillow `ImageGrab.grab(xdisplay=":109")`), inspect it, and click
the relevant button. Do not copy coordinates between dialogs. Aborting a failed
prelaunch task is different from choosing to debug anyway.

The optional `scripts/ide-check.sh` helper is an existing **connection smoke
check only**. It assumes the claude-sandbox driver, a single/default container,
and a sandbox-specific terminal probe. It normally closes VS Code on exit;
`KEEP_VSCODE=1` keeps it open. Read it before use and retain its driver settings
if continuing that session. Its screenshot does not prove a breakpoint or edit.
For multi-container targets, launch directly with the explicit `--target` above.

The optional `scripts/hotfix-cycle.sh` helper edits through a shell and exercises
stop/start/restart. It is not the VS Code acceptance test and must not replace
the editor change or breakpoint stages. It does not restore its edit on exit;
use only for separately requested lifecycle diagnostics with explicit cleanup.

Close only the instance created for the run (the headless driver may support
DevTools `Browser.close`). Capture evidence first and do not close unrelated
user windows.
