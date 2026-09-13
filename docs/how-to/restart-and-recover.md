# Restart, recover and retire a hotfix

Manage the hotfix lifecycle for your application. The commands use BlueAPI on
P47 as an example; substitute your pod, container and claim names.

## Restart after source or dependency changes

On your workstation:

```bash
podbench hotfix restart p47-blueapi-0 --container blueapi
```

For changes to `pyproject.toml` or `uv.lock`, attach a seat to the same container
and reinstall the Python environment before restarting:

```bash
podbench attach p47-blueapi-0
podbench hotfix restart p47-blueapi-0 --container blueapi --reinstall
```

`--reinstall` requires a running seat for that application container. It does not
compile native IOC code. If the child needs longer than the default two-minute
restart deadline, use `--deadline SECONDS` and check that its probes allow that
startup time.

After restarting, rerun `podbench ide vscode p47-blueapi-0` to
refresh debug launchers. Always select a **Podbench** launcher.

## Recover after debugging or a failed restart

```bash
podbench status
kubectl logs p47-blueapi-0 -c blueapi --tail=80
kubectl get pod p47-blueapi-0
```

`HELD` means probe protection remains active. Stop the debugger, fix or undo your
source change, then run `hotfix restart` again. Python injection remains in the
process after disconnecting; a successful child restart removes it and clears
the hold. Do not merely delete the hold file while the application is still
stopped or unhealthy.

## Save work and return to the image

In the seat, inspect `git diff` and save your changes to the application's normal
source repository before retiring the claim. For Git over SSH, a new IDE session
can forward your loaded local agent:

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
