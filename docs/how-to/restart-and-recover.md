# Restart, recover and retire a hotfix

Manage the hotfix lifecycle for your application. The commands use BlueAPI on
P47 as an example; substitute your pod, container and claim names.

## Restart after source or dependency changes

On your workstation:

```bash
podbench restart p47-blueapi-0 --container blueapi
```

The checkout is installed in editable mode by `uv sync`; ordinary Python source
edits do not need reinstalling. If you change dependency declarations in
`pyproject.toml` or versions in `uv.lock`, use `--reinstall` to rerun `uv sync`
before restarting. This requires a seat attached to the same container:

```bash
podbench attach p47-blueapi-0
podbench hotfix restart p47-blueapi-0 --container blueapi --reinstall
```

`--reinstall` requires a running seat for that application container. It does not
compile native IOC code. If the child needs longer than the default two-minute
restart deadline, use the hotfix restart command with an explicit timeout:

```bash
podbench hotfix restart p47-blueapi-0 --container blueapi --deadline 300
```

This allows five minutes. Check that the application's probes allow that startup
time too. The shorter `podbench restart` command does not expose `--deadline`.

Inside a configured seat, `podbench stop`, `podbench start` and `podbench restart`
infer the target without Kubernetes credentials. Workstation equivalents accept
the pod plus `--container`, namespace and context options. Start/Stop are harmless
when already in the requested state; Start refuses while Launch owns the process.

Start reruns the supervisor's full startup command. VS Code **Podbench: Launch — …**
runs only the captured process invocation and requires an explicit Stop first.
VS Code Stop/Restart controls the Launch session. **Podbench: Attach — …** resolves
the current process each time; ordinary restarts need no workspace regeneration.

## Recover after debugging or a failed restart

```bash
podbench status
kubectl logs p47-blueapi-0 -c blueapi --tail=80
kubectl get pod p47-blueapi-0
```

Status distinguishes `normal`, `stopped`, `debugger`, `starting`, `failed` and
`HELD`. Ending Launch intentionally leaves the application stopped and held;
run Start to resume normal execution. After failed startup, repair the source or
health check and retry Start. Do not simply delete the hold file.

Attach disconnect leaves the application running and releases its own hold.
Injected debugpy remains until an application restart. Restart reruns initialization
and removes that injection. Pod/container replacement discards stopped/debugger
state and starts normally, while the checkout survives on its claim.

## Save work and return to the image

In the seat, inspect `git diff` and save your changes to the application's normal
source repository before retiring the claim. If you need Git over SSH, run the
following **on your workstation** to open an IDE session with agent forwarding:

```bash
ssh-add ~/.ssh/id_ed25519
podbench ide vscode p47-blueapi-0 --forward-agent
```

Git name and email are copied to the seat; private keys stay local. Any process
running as the seat's UID can use the forwarded agent while connected, so use it
only for a session you trust. Initialization of a private repository may need
separate Git authentication; IDE forwarding is scoped to its SSH connection.

Remove the generated hotfix workload settings, wrapper template and chart
dependency in the service repository, preserving its pre-existing settings.
Review and deploy that change through the normal beamline process. Verify the
replacement pod is healthy and no longer mounts the hotfix claim.

Then check retirement from your workstation:

```bash
podbench hotfix retire pvc/p47-blueapi-podbench-project
```

When it reports that the claim is unmounted, and the work is saved, delete it:

```bash
podbench hotfix retire pvc/p47-blueapi-podbench-project --delete-claim
```

Claim deletion discards the persistent checkout. The command refuses retirement
while a pod still mounts it. A normal end to a debugging session does not require
retiring the claim.
