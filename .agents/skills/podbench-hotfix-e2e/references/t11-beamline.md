# T11 beamline targets

Observed 2026-09-14; verify all paths, versions and live state before reuse.
Do not reuse recorded PIDs, seat names or holds as current identities.

## Access and targets

- Namespace `t11-beamline`, API `https://192.168.1.81:6443`. A network-restricted
  sandbox needs access to that address; unrestricted environments need no
  sandbox-specific setup.
- Scoped kubeconfig: `k8s/t11-beamline-agent-giles.kubeconfig`, context/service
  account `agent-giles`. Attach and hotfix RBAC passed in the walkthrough.
- Services repository: `gilesknap/t11-services`, deployed by Argo CD. Wiring is
  in `services/RELEASE/values.yaml`; the claim chart pin is in
  `.helm-shared/Chart.yaml`. Inspect local CLI help before generating updates.
- The walkthrough found kubectl at `/cache/bin/kubectl`, outside PATH. Export
  that directory in the test environment rather than assuming it is missing.
- Local SSH identity and Include were missing; creating the key and running
  `podbench doctor --fix -n t11-beamline` resolved them without user action.

| Pod | Container | Useful code path |
|---|---|---|
| `t11-blueapi-0` | `blueapi` | `/podbench/app/src/blueapi/service/main.py`, `health_probe()`, triggered by `/healthz` |
| `bl11t-mo-sim-01-0` | `bl11t-mo-sim-01` | `/epics/support/motorMotorSim/motorSimApp/src/motorSimDriver.cpp`, `motorSimAxis::process()`, called periodically without commanding motion |

Both selected pods had initialized claims and lifecycle supervisors. Other
listed targets included legacy wiring; selection does not prove readiness.

## BlueAPI recipe and result

The claim already contained a tutorial log line in `health_probe()`. Save and
preserve it as the baseline rather than resetting to Git HEAD. Set a breakpoint
on that line in the generated running-process Attach configuration, trigger a
health request, and inspect the paused AnyIO worker's source, stack and globals.
For the edit demonstration, replace only the log message in the VS Code editor
with a unique per-run marker; save, disconnect, restart, and check a new health
request plus timestamped logs. Restore the original line through the editor and
restart again.

The walkthrough passed all these stages. The pod UID was unchanged, restart
count stayed 0, health returned `{"status":"ok"}`, supervisor was normal and
holds were empty after cleanup. An earlier dead debugger had left stale ownership
and a hold; see [debugger recovery](debugging.md) before handling that condition.
The existing tutorial source edit was preserved.

BlueAPI startup loads devices and can take roughly 40–60 seconds or longer.
Use bounded lifecycle/health checks with progress updates, not a fixed short
sleep. The seat's older Podbench lacked top-level `restart`; the workstation
CLI applied the changes. Check actual seat and workstation versions each run.

## mo-sim recipe and incomplete result

The pre-existing claim edit was an `echo "PODBENCH_HOTFIX_MOTORSIM=1"` in
`ioc/start.sh`; `.podbench-hotfix.json` and `.podbench-ptrace.so` were also
present. Preserve these. The claim startup invokes a stdio wrapper which then
runs the image's IOC startup, so track the actual IOC separately from its
supervised wrapper.

The developer image contained source, make/dependencies, and debug information
in `/epics/support/motorMotorSim/lib/linux-x86_64/libmotorSimSupport.so`. The
claim did not contain that native module. A run-specific copy under the claim
built successfully with the image's EPICS dependencies. For a future native
edit demonstration, edit that copy through VS Code, rebuild, and arrange a
reversible library-path override before startup's stdio delegation. Verify the
actual IOC maps the rebuilt library. This load/edit route was **not tested**.

The native breakpoint hit source line 401 (requested line 400), with locals and
the simulation-thread stack visible. Explicit x64 configuration and a UI source
location breakpoint workaround were needed; see [debugger recovery](debugging.md).
Stopping that session killed the IOC and restarted the container: restart count
0 became 1. This failed the lifecycle assertion. Do not repeat the native Stop
route tomorrow; establish safe detach behaviour first.

The user postponed further work. Native editor change, rebuilt-library loading
and changed output remain **unrun**. Cleanup removed the temporary module copy,
preserved the exact original startup diff, closed the dedicated VS Code and
left the application Ready/normal with no holds. The pod UID was unchanged;
a recovery seat `podbench-2` remained, while the old seat had terminated.

## Evidence and next run

The local walkthrough artifacts were saved under
`tmp/hotfix-e2e/20260914-walkthrough/`, with one directory per pod containing
screenshots, a report, source diffs, logs and Kubernetes snapshots. These local
artifacts are not shipped with the skill; their absence in another checkout is
not a test failure. Capture new evidence for every rerun.

Start the next run with fresh access/status/ownership checks and pod selection.
BlueAPI is a demonstrated Python route. mo-sim still needs a safe native detach
route and the full editor/rebuild/load demonstration. Neither the restart failure
nor the stale-ownership cleanup bug was fixed by updating this skill.
