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

An `initialized` workload is ready to use. A workload marked `ready for init`
needs only the initialization step below. Check the current state before making
changes, even if the workload has been used for hotfix before.

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
writes the BlueAPI wrapper template. Review all generated files, including new
ones shown by `git status --short`, then deploy through the beamline's normal
review and GitOps process. Wait for the replacement pod to become ready before
continuing. Keep the workload at one replica.

For an IOC the equivalent service directory is `services/bl47p-mo-ioc-01` and
container `bl47p-mo-ioc-01`. Existing values such as `volumes` or `volumeMounts`
can conflict with generated values. In that case, use `hotfix values` and merge
the settings with the existing lists instead of replacing them.

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
use `hotfix restart` when ready to run the checkout.

For a fresh PMAC IOC claim:

```bash
podbench hotfix init bl47p-mo-ioc-01-0 --container bl47p-mo-ioc-01 \
  --repo https://github.com/epics-containers/ioc-pmac.git --ref 2026.6.1
```

Non-Python repositories are cloned without a build step. Init refuses to replace
files on a non-empty claim, including a partially initialized one. Inspect and
back up any contents before deciding how to recover; never clear a shared
checkout merely to repeat the tutorial.

Now follow [edit and debug an application](../tutorials/hotfix.md).
