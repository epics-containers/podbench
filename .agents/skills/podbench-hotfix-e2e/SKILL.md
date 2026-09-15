---
name: podbench-hotfix-e2e
description: >-
  Demonstrate Podbench hotfix end to end on user-selected live pods through
  podbench ide vscode: hit source breakpoints, capture screenshots, edit in
  the VS Code editor, and prove the running application uses the change.
  Ask which kubeconfig and namespace to use first, print resource setup
  guidance, then ask which pods to test, and narrate progress with evidence
  throughout the run.
---

# Podbench hotfix end-to-end

Run a real VS Code debugging and editing demonstration. Completion requires
an actual source breakpoint hit, a saved editor change, and observed application
behaviour caused by that change for each selected target. A shell edit, direct
DAP test, or connected terminal alone does not satisfy this workflow.

## Execution contract

When subagents are available, delegate the selected pod's detailed UI execution
and evidence capture to one worker. The main agent owns user interaction and
reports concise milestones, useful screenshots and blockers; avoid narrating
raw commands or forwarding tool-output transcripts. Give the worker the exact
selected pod/container, environment, baseline, evidence directory and cleanup
requirements. Only one agent may drive a given VS Code instance or mutate a
given target. While the worker runs, the main agent can inspect prerequisites
for the next target read-only. Relay worker observations as they arrive, inspect
its evidence, and keep the user updated during waits. If delegation is not
available, execute directly with the same concise user-facing commentary.

Follow the numbered stages in order, one selected pod at a time. Keep a short
progress checklist in the conversation with each stage marked pending, running,
passed, failed or blocked. Mark a stage passed only after observing its required
result. Finish the demonstration and cleanup for one pod before starting another.

This skill is agent-neutral: use the shell and actual VS Code UI tools available
in your environment. No Codex-specific API is required. When using a separate
headless driver, read its skill and command help first; do not invent commands.
Read [VS Code driving](references/vscode-driving.md) before opening the IDE,
and [debugger preparation and recovery](references/debugging.md) before attaching.
The latter includes a native Stop failure that must be addressed before a rerun.

The user-facing opening must ask which cluster to use, then contain setup
guidance, followed by the candidate pod list and the pod-selection question.
After the user selects targets, carry out the test rather than stopping at a
plan. Do not ask the user to perform UI
steps the available driver can perform.

| Stage | Evidence required to pass |
|---|---|
| Connect | Correct remote target and claim folder visibly open in VS Code. |
| Breakpoint | Triggered execution stopped on the chosen source breakpoint, with source, stack and variables inspected; screenshot captured. |
| Edit | Reversible change typed into the VS Code editor and saved to the claim; screenshot and diff recorded. |
| Apply | Required rebuild/restart completed; child and container evidence recorded. |
| Observe | Fresh application output demonstrates the intended change; screenshot and underlying output recorded. |
| Restore | This run's edit undone, baseline behaviour and healthy lifecycle restored. |

Before each UI action, inspect the current window/snapshot and establish focus.
After the action, inspect the result before proceeding. If an action fails,
check window, connection and focus, then retry the specific action once. If it
still fails, report the failed action and observed UI state, capture a diagnostic
screenshot, and restore the workload. Do not repeat blind keystrokes, switch to
shell editing, or silently skip a stage. A diagnosed recoverable issue can be
fixed and the failed stage resumed; explain that recovery in commentary.

Example commentary: “blueapi: the breakpoint is bound. I am triggering the
request now.” Then: “Execution stopped at main.py:123; the stack shows the
request handler. Here is the paused-source screenshot.” Use actual observed
names and values, never the example as claimed evidence.

## 1. Ask which cluster, print setup guidance, then ask which pods

Before any cluster command, establish the target cluster. List the kubeconfig
files found in the checkout (for example `k8s/*.kubeconfig`), any `KUBECONFIG`
already set, and their contexts, servers and namespaces, then ask: **Which
kubeconfig, context and namespace should this run use?** Wait for the answer.
Do not infer the cluster from a reference file, a previous run or a handoff;
those record one past target, and the repository may hold credentials for
several beamlines. If reaching the chosen API server needs a VPN, SSH tunnel
or sandbox network allowance, ask how that access is provided in the same
question. Use the chosen kubeconfig, context and namespace explicitly in
every `kubectl` and `podbench` command for the rest of the run.

Then show the user the resources below, how to prepare them, and which are
already available. Inspect the environment and current CLI help. Run the
checkout with `uv run podbench` when appropriate. Do not assume the agent is
in a jail or that setup must happen outside it.

| Resource | Setup guidance |
|---|---|
| Network route to the API | The chosen API server may sit behind a VPN, an SSH tunnel (see `k8s/vpn-api-tunnel.sh`) or a sandbox network policy. State which route applies, whether it is currently working, and exactly what the user must provide (VPN session, SSH host and key, allowed address) if it is not. |
| Cluster access and kubectl | Install kubectl, set `KUBECONFIG` to the credentials chosen above, and verify API access with a read-only call. Report the actual error when access fails rather than guessing at its cause. |
| RBAC and SSH | Run `podbench doctor -n NAMESPACE`. Check exec and ephemeral-container permissions, including `kubectl auth can-i update pods --subresource=ephemeralcontainers -n NAMESPACE`. Create an SSH key if missing and use `podbench doctor --fix` to configure Podbench SSH. |
| Hotfix workload and storage | The workload needs a writable claim at `/podbench/app`, lifecycle supervisor wiring and probes suitable for debugging pauses. Generate the service-chart change with `podbench hotfix enable services/RELEASE --from-pod POD --container NAME -n NAMESPACE`; inspect the original entrypoint, mounts and probes, then deploy through the environment's normal workflow. |
| Editable source and runtime | Populate an empty claim with `podbench hotfix init POD --repo URL --container NAME -n NAMESPACE`. Verify the application runs the claim's source and has its dependencies. Preserve existing claim contents. Native targets also need matching source, symbols and a usable rebuild path. |
| VS Code and UI automation | Provide VS Code, Remote-SSH, and an agent-accessible desktop/UI driver with screenshot support. For headless Linux, provide a virtual display and a VS Code UI driver; see [VS Code driving](references/vscode-driving.md). `podbench ide vscode` prepares the seat and debugger launchers. |

The purpose of this stage is to surface every prerequisite the user has to
provide, so the run is not blocked later. Clearly label setup information as
**guidance — no action needed now**. Check and handle available setup
yourself. If the user must do something, label it **Action needed from you**,
give the exact command or decision, and explain why you cannot do it. Do not mix hypothetical setup commands with actual requests.

Print setup commands as guidance before making workload changes. Missing
resources are setup blockers to describe precisely. Do not require GitHub
push credentials for a local editor demonstration.

Then list candidates with `podbench status -n NAMESPACE`, including HOTFIX
state, and ask: **Which pods and application containers would you like me to
test?** Wait for the selection before live mutations. Do not pick a
substitute pod.
Read [T11 target notes](references/t11-beamline.md) only when testing T11 and
[P47 target notes](references/p47-beamline.md) only when testing P47; verify
the recorded values against live state.

For each selected target, identify a reachable source line, how to trigger it,
a small reversible edit, and an observable before/after result. A shell startup
marker alone does not prove source debugging. If a target lacks source/symbols
or a debugger path, report the missing prerequisite for that target.

## 2. Narrate and record the baseline

Give running commentary before each meaningful action and after its result:
setup checks, target selection, IDE connection, breakpoint placement, trigger
and hit, editor change, restart/rebuild, behaviour check, and cleanup. State the
pod, what you are doing, and what the observation proves. During longer waits,
report progress at least every 60 seconds; bound waits and describe failures.
Do not leave the user with only a final report or raw command output.

Before mutations record pod UID, container, supervised child PID, container
restart count, lifecycle state, hold state, readiness, current application
response and the source diff/content needed to undo this run's edit. Preserve
pre-existing edits. Use the live-state and cleanup principles in
[podbench-live-testing](../podbench-live-testing/SKILL.md); the IDE sequence below
is the acceptance path for this skill.

Legacy wiring must be updated through the service repository and normal
rollout workflow. Prepare a reviewable diff; do not commit without asking the
user. After an authorized rollout, verify the lifecycle control channel and
record a fresh baseline for the replacement pod.

`podbench hotfix enable` is itself under test. Start from the service's
pre-hotfix values (restore them from Git history if an older enable already
wrote wiring) and let `hotfix enable` produce every line of the wiring. If it
refuses, errors, or emits wiring that is incomplete, wrong or destructive (for
example dropped volumes, probes or comments, a wrong entrypoint, a nested
legacy loop, a missing dependency), that is a product defect: stop, record
the exact command and output as a finding, and fix the product before
continuing. Do not hand-merge `hotfix values` fragments, edit generated
blocks, delete keys to get past a refusal, or otherwise work around the tool.
A hand-assembled wiring proves nothing about `hotfix enable`, and a run built
on one cannot pass the Apply stage.

## 3. Open VS Code and hit a breakpoint

1. Run `podbench ide vscode POD -n NAMESPACE --target NAME` (check current help
   for supported options). Drive the resulting VS Code window using UI tools.
2. Verify the correct Remote-SSH workspace and claim source are open. Capture
   a connected-workspace screenshot identifying the target.
3. Open the actual application source in the editor. Set a breakpoint through
   the gutter or editor command, select the generated debugger configuration
   for the application process, and start debugging through VS Code.
4. Trigger that code path and wait for a **bound breakpoint to be hit**. Confirm
   VS Code shows execution paused at the expected source line, a relevant call
   stack and useful variables. Merely placing a red dot or pressing Pause is
   insufficient.
5. Capture and inspect a screenshot while paused, with the source file, line,
   execution highlight and debug panels visible. Link the image in commentary
   and explain the observed code/stack/value. Resume promptly after inspection.

## 4. Edit in VS Code and demonstrate the result

1. Use the VS Code **text editor** to make the planned reversible source edit
   and save it. Shell commands, terminal editors, filesystem tools, and direct
   file writes cannot stand in for the demonstrated edit. Capture the changed
   source with its saved state visible; verify the diff landed in the claim.
2. Disconnect debugging without terminating the application; for native sessions,
   follow the detach preflight in [debugger recovery](references/debugging.md).
   Verify the container did not restart, then apply the edit using the supported
   lifecycle. Run
   `podbench restart POD -n NAMESPACE --container NAME` from the VS Code remote
   terminal when available, or the workstation CLI if the seat version lacks
   it. For compiled code, rebuild the edited source with the target's build
   procedure before restart. Explain the chosen route in commentary.
3. Re-trigger the same application behaviour. Show the baseline and changed
   response, value or newly emitted unique log marker. Scope logs to this run;
   a typed command or old log entry containing the marker is not proof.
4. Capture a screenshot showing the actual changed output, ideally alongside
   the edited source in VS Code. Keep the underlying response/log evidence too.
   Confirm child replacement, unchanged pod UID/container restart count,
   restored health, normal supervisor state and released hold. A container
   restart or replacement pod must be reported and cannot pass that assertion.

Refresh the IDE launchers after a process restart before further debugging;
they may refer to the old PID. If useful, hit the changed line again and capture
its new value. Report any failed stage as incomplete; do not replace it with a
weaker check and declare success.

## 5. Restore and report

If the user stops or postpones the run, stop new demo work and have the worker
finish only necessary cleanup. Report a clear resume point with unrun stages.

Undo only this run's edit through the VS Code editor, save, rebuild if needed,
and restart to remove injected debugging state. Verify baseline behaviour,
health, lifecycle state and release of the hold. Cleanup also applies after a
failed intermediate step; preserve evidence and report anything not restored.
Keep a demo change only when the user asks. Remove only disposable resources
created by this run; preserve existing claims, seats and user work. Close this
run's VS Code instance after saving screenshots.

Store real UI screenshots in a unique persistent directory such as
`tmp/hotfix-e2e/RUN/POD/` with descriptive filenames: `01-connected.png`,
`02-breakpoint-hit.png`, `03-edit-saved.png`, `04-change-observed.png`.
Inspect each capture for legibility and relevance, and retake if necessary.
Screenshots must come from the live UI, never generated or reconstructed.
Show linked images as the run progresses and in the final report; redact secrets
by choosing a safe view before capture. Pair each image with a short caption
explaining what it demonstrates.

Report per pod: breakpoint file/line and observed stack/value, edit and
before/after behaviour, child PID and restart evidence, screenshot links,
cleanup outcome, and any blocker. Clearly separate passed stages from unrun or
failed stages. Updating this skill alone is not evidence of a live test pass.
