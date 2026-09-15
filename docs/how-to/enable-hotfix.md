# Prepare a workload for hotfix

Hotfix needs a single-replica workload, a persistent claim mounted at
`/podbench/app`, and a supervisor that can restart the application child. Do this
once, before following the [hotfix tutorial](../tutorials/hotfix.md). The commands
use BlueAPI and a PMAC IOC on P47 as examples; substitute your service repository,
pod, container and application source as appropriate.

## Check existing wiring first

```bash
podbench status
```

Read both parts of the `HOTFIX` column: the checkout state and the supervisor
state. For example:

| HOTFIX | Next step |
| --- | --- |
| `initialized, normal` | Already prepared; continue with the hotfix tutorial. |
| `ready for init, normal` | Wiring is in place; initialize the empty claim below. |
| `initialized, legacy wiring` | Update the wiring and roll out the pod; keep the existing checkout. |
| `ready for init, legacy wiring` | Update the wiring and roll out the pod, then initialize the empty claim. |
| `initialized, normal, HELD` | A probe hold is active; finish or coordinate the existing debugging session before continuing. |

`ready for init` alone does not confirm that the supervisor is current. Check
the full state even if the workload has been used for hotfix before.

`HELD` can accompany other states too, such as `stopped` or `failed`. Follow
[restart and recovery](restart-and-recover.md) to resolve the hold before
continuing; do not delete the hold file manually.

## Generate and deploy the chart changes

Work in your service repository. For example, to prepare BlueAPI in
[p47-services](https://github.com/epics-containers/p47-services):

```bash
module load ec/p47
cd p47-services
podbench hotfix enable services/p47-blueapi \
  --from-pod p47-blueapi-0 --container blueapi
git diff
```

The command edits the local service chart; it does not deploy it. It derives the
live identity and entrypoint, adds the claim dependency and workload values, and
writes a small entrypoint wrapper for BlueAPI. The dependency supplies the shared
Bash functions in a ConfigMap mounted at `/podbench/runtime`; application arguments
contain only the invocation, never the supervisor implementation. Review all
generated files, including new ones shown by `git status --short`, then deploy through the beamline's normal
review and GitOps process. Wait for the replacement pod to become ready before
continuing. Keep the workload at one replica.

Re-run `hotfix enable` to replace older inline wiring and update the dependency
version, then refresh your chart dependencies and deploy. Runtime-only ConfigMap
updates may not trigger a rollout: the running supervisor has already loaded its
functions, and BlueAPI also mounts its entrypoint through `subPath`. After GitOps
deploys a runtime update, arrange an interruption window and replace the pod
through a rollout:

```bash
kubectl rollout restart statefulset/p47-blueapi
kubectl rollout status statefulset/p47-blueapi
podbench status
```

The claim survives; the old seats are replaced. Use the actual controller name
and kind for another workload. `hotfix enable` reporting `unchanged` means the
local files already match; it does not confirm that the running pod uses them.

After the rollout, an empty claim should show `ready for init, normal`. An
existing initialized claim should show `initialized, normal`; skip initialization
for that claim.

Stop and Launch require hold-aware exec liveness probes or no liveness probe.
Extending an HTTP/TCP/gRPC failure threshold cannot protect an indefinite stop.
Readiness remains active. Start checks configured health before releasing its
hold; generic HTTP checks need `curl` and gRPC checks need `grpc_health_probe`
in the application image.

For an IOC the equivalent service directory is `services/bl47p-mo-ioc-01` and
container `bl47p-mo-ioc-01`. Existing `volumes` and `volumeMounts` lists keep
their entries and gain the generated ones. Other wiring keys the service already
sets, such as `command`, `args` or probes, are replaced by the generated block;
pass `--entrypoint` when the original command must be kept.

:::{admonition} Other charts or conflicting values
Print the wiring for manual integration:

```bash
podbench hotfix values --app YOUR_RELEASE --from-pod YOUR_POD \
  --container YOUR_CONTAINER
```

Use `--values-prefix KEY` for a wrapper chart's workload settings; claim settings
stay at the top level. Use `--entrypoint 'COMMAND'` when the original command does
not select code under `/podbench/app`. Review and deploy the dependency and values
using your chart's normal process.
:::

## Other application entrypoints

The same Bash supervisor runs Python, shell scripts, and native executables.
It inherits the container's environment, working directory, user and umask.
Bash and `setsid` must be present in the application image; the supervisor does
not need Python or Podbench installed there.

Use `--entrypoint 'COMMAND'` to choose how the application starts. A command
normally runs unchanged. Two shared functions select an initialized checkout:

```bash
# Keep the original interpreter until the checkout's Python environment exists.
podbench_python /opt/app/.venv/bin/python -m myapp serve

# Run an editable Bash startup script when present; otherwise run the original command.
podbench_script /podbench/app/start.sh bash /opt/app/start.sh
```

Pass the entire function call as `--entrypoint`. For a native executable, pass
its command and arguments directly. Building native code remains your project's
responsibility. The IOC and BlueAPI chart adapters supply their known startup
paths; other application layouts use the same runtime with explicit paths.

## Initialize an empty claim

For BlueAPI, choose a branch or tag compatible with the deployment's configuration:

```bash
podbench hotfix init p47-blueapi-0 --container blueapi \
  --repo https://github.com/DiamondLightSource/blueapi.git --ref 1.17.0
```

`1.17.0` is the image version observed on P47, not a requirement for existing
checkouts. Keep an already initialized checkout at its agreed revision.
Initialization clones the repository, syncs Python projects with uv, adds debugpy,
and records the source revision and base image. It does not restart the child;
use `podbench restart POD` when ready to run the checkout.

For a fresh PMAC IOC claim:

```bash
podbench hotfix init bl47p-mo-ioc-01-0 --container bl47p-mo-ioc-01 \
  --repo https://github.com/epics-containers/ioc-pmac.git --ref 2026.6.1
```

Non-Python repositories are cloned without a build step. Init refuses to replace
files on a non-empty claim, including a partially initialized one. Inspect and
back up any contents before deciding how to recover; never clear a shared
checkout merely to repeat the tutorial.

Run `podbench status` again after initialization. The checkout state should have
changed from `ready for init` to `initialized`, giving `initialized, normal` when
the application is running normally.

Now follow [edit and debug an application](../tutorials/hotfix.md).
