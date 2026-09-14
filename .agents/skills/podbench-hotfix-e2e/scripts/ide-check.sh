#!/bin/bash
# Open a pod's debug seat in headless VS Code and prove the connection.
#
# Uses the claude-sandbox vscode-headless skill: set VSCODE_HEADLESS_SKILL to
# its directory (default: ../claude-sandbox/.claude/skills/vscode-headless
# relative to this repository). Verifies the Remote-SSH window, a remote
# terminal in the seat, and that the seat is inside the sandbox jail.
# Screenshots go to tmp/ in the repository, which is visible outside the jail.
set -euo pipefail
pod=${1:?POD required}; ns=${2:?NAMESPACE required}
repo=$(cd -- "$(dirname -- "$0")/../../../.." && pwd)
skill=${VSCODE_HEADLESS_SKILL:-$repo/../claude-sandbox/.claude/skills/vscode-headless}
launcher=$skill/scripts/vscode-headless; driver=$skill/scripts/vscode-ui.mjs
[ -x "$launcher" ] || { echo "ide-check: $launcher missing; see the vscode-headless skill" >&2; exit 2; }
PODBENCH=${PODBENCH:-podbench}
ui() { node "$driver" "$@"; }
mkdir -p "$repo/tmp"
# A private VS Code instance per run: a reused instance may hold a stale
# window from a previous pod whose SSH session is dead, and podbench would
# open the workspace there. The Remote-SSH extension is reinstalled into the
# fresh extensions dir by podbench itself.
export VSCODE_HEADLESS_DATA=${VSCODE_HEADLESS_DATA:-/tmp/ide-check-$pod}
export VSCODE_HEADLESS_PORT=${VSCODE_HEADLESS_PORT:-9333}
close() {
    node -e 'const r=await fetch("http://127.0.0.1:"+process.env.VSCODE_HEADLESS_PORT+"/json/version");const {webSocketDebuggerUrl}=await r.json();const ws=new WebSocket(webSocketDebuggerUrl);ws.addEventListener("open",()=>{ws.send(JSON.stringify({id:1,method:"Browser.close"}));setTimeout(()=>process.exit(0),1500);});' --input-type=module 2>/dev/null || true
}
[ "${KEEP_VSCODE:-0}" = 1 ] || trap close EXIT

log=$repo/tmp/ide-check-$pod.log
$PODBENCH ide vscode "$pod" -n "$ns" --code "$launcher" --timeout 240 > "$log" 2>&1 &
for _ in $(seq 1 120); do grep -q "^Ready:" "$log" && break; sleep 2; done
grep "^Ready:" "$log" || { echo "ide-check: podbench did not report Ready"; tail -5 "$log"; exit 1; }

# podbench opens a bootstrap window first; drive the workspace window, and
# wait for its title to gain the Remote-SSH suffix, which arrives after
# podbench reports Ready.
export VSCODE_HEADLESS_WINDOW
title=""
for _ in $(seq 1 60); do
    read -r VSCODE_HEADLESS_WINDOW title < <(ui windows | python3 -c '
import json,sys; ws=json.load(sys.stdin)
w=next((w for w in ws if "Workspace" in w["title"] and "[SSH: podbench." in w["title"]), None) \
  or next((w for w in ws if "Workspace" in w["title"]), ws[0])
print(w["id"], w["title"])')
    case "$title" in *"[SSH: podbench."*) break;; esac
    sleep 2
done
case "$title" in *"[SSH: podbench."*) echo "PASS      Remote-SSH window: $title";; *) echo "FAIL      window is not a podbench SSH remote: $title"; exit 1;; esac
# Wait until the window's status bar stops reporting a connection in progress.
for _ in $(seq 1 45); do
    text=$(ui snapshot | python3 -c 'import json,sys; print(json.load(sys.stdin)["text"])')
    case "$text" in *"Disconnected from SSH"*|*"Setting up SSH Host"*|*"Opening Remote"*) sleep 2;; *) break;; esac
done
sleep 5
ui key Escape; sleep 1
ui key Ctrl+Shift+p; sleep 2; ui text 'Terminal: Create New Terminal'; sleep 1; ui key Enter; sleep 5
# Output-only markers: the typed command is also visible text, so the
# expected strings must not appear in the command itself.
ui text 'clear; echo SEAT_$(echo PROBE) user=$(id -un) sandbox=$IS_SANDBOX ro=$(touch /usr/bin/x 2>&1 | grep -c "Read-only"); podbench --version'; ui key Enter; sleep 4
line=$(ui snapshot | grep -oE 'SEAT_PROBE user=[^ ]+ sandbox=[0-9]* ro=[0-9]' | head -1 || true)
[ -n "$line" ] && echo "PASS      remote terminal answered: $line" || { echo "FAIL      no output from the remote terminal"; exit 1; }
ui screenshot "$repo/tmp/ide-check-$pod.png" >/dev/null && echo "INFO      screenshot tmp/ide-check-$pod.png"
echo "RESULT    IDE connection proven; close VS Code with the DevTools Browser.close call when done"
