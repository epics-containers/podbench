# P47 beamline targets

Observed 2026-09-15; verify paths, versions and live state before reuse. Do not
reuse recorded PIDs, seat names or holds as current identities.

## Access and targets

- Namespace `p47-beamline`, API `https://api.pollux.diamond.ac.uk:6443`; a
  network-restricted sandbox needs that address allowed.
- Scoped kubeconfig `k8s/p47-beamline-agent-hgv27681.kubeconfig`, context
  `agent-hgv27681`. Attach and hotfix RBAC passed.
- Services repository `epics-containers/p47-services`, deployed by Argo CD from
  `epics-containers/p47-deployment` (auto-sync, self-heal; fastcs is deployed
  from branch `podbench-hotfix-claim`, the rest from `main`). The claim chart is
  vendored at `.helm-shared/podbench-hotfix-claim` with `file://` dependencies.
- Argo Application objects are not visible from the cluster; judge sync by
  StatefulSet generation and ConfigMap appearance (about 2 minutes after push).

| Pod | Container | Useful code path |
|---|---|---|
| `p47-blueapi-0` | `blueapi` | `src/blueapi/service/main.py` `health_probe` (~line 618), hit by the kubelet's own probe every ~10 s |
| `bl47p-ea-fastcs-01-0` | `bl47p-ea-fastcs-01` | `src/fastcs_example/controllers.py` `TemperatureControllerHandler.update` (0.2 s poll, ~line 53); marker PV `T01-EA-FASTCS-01:HotfixMarker` |
| `bl47p-mo-ioc-01-0` | `bl47p-mo-ioc-01` | `/epics/support/pmac/pmacApp/src/pmacController.cpp` `pmacController::poll` (motorPoller thread) |
| `bl47p-ea-simdet-01-0` | `bl47p-ea-simdet-01` | `simDetector::computeImage`, triggered by one frame on `BL47P-EA-SIMDET-01:DET:Acquire`; one claim shared by -01, -02, -03 |

## Recipes that passed

- BlueAPI: LOGGER.info marker in `health_probe`; Disconnect releases the hold;
  seat-side `podbench restart` takes ~60 s; healthz shows the marker in logs.
  Startup needs ~70 s (device connection timeouts); startup budget 180 s.
- fastcs: change `hotfix_marker` initial_value in the editor; `hotfix restart`
  ~1 s; read the PV. The claim needed `hotfix restart --reinstall` once to gain
  the ptrace shim.
- Native IOC (mo-ioc): printf marker in the claim's `ioc/iocApp/src/iocMain.cpp`
  plus `export IOC=/podbench/app/ioc` at the top of the claim's `ioc/start.sh`;
  build with `kubectl exec ... -- bash -c 'cd /podbench/app/ioc && make'` (the
  seat has no make); `hotfix restart` (~2 s) runs the claim binary; restore by
  undoing both edits and removing build outputs.

## Hazards seen here

- BlueAPI chart keeps a 1.7G venv emptyDir under a 2Gi ephemeral limit; the
  seat evicted the pod until the limit was raised to 6Gi (podbench#269).
- Headroom of 2 CPU/4Gi requests was refused by node and quota, and is never
  released after a session; revert a stale or pending resize to the
  `podbench.io/ide-resources` baseline with a resize patch.
- Seat preparation without headroom tripped the IOC's 1 s exec liveness probe
  (0.5 CPU container) and restarted it; seats die with the app container.
- A StatefulSet rolling update stalls behind a crash-looping pod; delete the
  pod to let the new template through.
