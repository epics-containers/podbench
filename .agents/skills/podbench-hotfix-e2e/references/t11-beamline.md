# T11 beamline targets

Values from the 2026-09-14 run. Verify before use.

- Namespace `t11-beamline`, API `https://192.168.1.81:6443`, so the sandbox
  needs `allow-ip = 192.168.1.81`.
- Scoped kubeconfig: `k8s/t11-beamline-agent-giles.kubeconfig`, service
  account `agent-giles`, with exec and ephemeral-container rights.
- Services repository: `gilesknap/t11-services`, branch `main`, deployed by
  Argo CD. Hotfix wiring lives in `services/RELEASE/values.yaml`; the claim
  chart pin is in `.helm-shared/Chart.yaml`.

| Pod | Container | Wiring | Visible edit | Expect in log |
|---|---|---|---|---|
| `t11-blueapi-0` | `blueapi` | lifecycle supervisor via wrapper ConfigMap | insert `LOGGER.info("MARKER")` in `health_probe()` in `src/blueapi/service/main.py` | `MARKER`, logged on every health probe |
| `bl11t-mo-sim-01-0` | `bl11t-mo-sim-01` | lifecycle supervisor, upgraded 2026-09-14 | insert `echo MARKER` at the top of `/podbench/app/ioc/start.sh` | `MARKER`, printed once at IOC start |

Timings seen: blueapi `start` about 40 s to healthy, `restart` about 45 s;
the IOC under 1 s for both. blueapi is slow to become Ready after a pod
roll, about 60 s, with startup-probe warnings in events meanwhile.

Seat image `ghcr.io/epics-containers/podbench:prototype-attach-hotfix`
shipped podbench 0.20.1.dev4 on 2026-09-14, without the top-level `restart`.
