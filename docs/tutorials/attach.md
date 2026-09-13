# Debug a running application with attach

:::{warning}
Attaching a debugger depends on the host's ptrace policy. This workflow worked on
DLS RHEL 8 servers with `ptrace_scope=0`; newer DLS RHEL 9 servers use a more
restrictive setting, which can prevent debugger attachment even when the seat
connects successfully.

Hotfix can work around this by making the application opt into debugging when it
restarts. Supporting plain attach on these hosts will require a small change to
the application containers we ship. Until then, use the [hotfix workflow](hotfix.md)
on affected servers.
:::

Inspect a running application without replacing its container. You will attach
a seat, read a backtrace, and open the same application in VS Code. Complete
[setup](setup.md) first.

The worked example is the PMAC IOC on P47, pod `bl47p-mo-ioc-01-0`. Substitute
your own pod name when following the workflow on another beamline or cluster.

## Attach a seat

On your workstation:

```bash
module load ec/p47
podbench attach bl47p-mo-ioc-01-0
```

This pod has one application container. For multi-container pods, add
`--target CONTAINER` to attach or IDE commands; otherwise Podbench selects the
first application container without an error.

Example output after completing setup:

```text
landed podbench-1 for p47-beamline/bl47p-mo-ioc-01-0/bl47p-mo-ioc-01
connect: ssh podbench.p47-beamline.bl47p-mo-ioc-01-0.1.a1b2c3d4
```

If a seat already exists, the first line says `reusing` instead of `landed`.
Run the command after `connect:` to enter the seat:

```bash
# Copy the SSH command from your own output; the alias suffix will differ.
ssh podbench.p47-beamline.bl47p-mo-ioc-01-0.1.a1b2c3d4
```

You are now **in the seat**, an ephemeral container with Git, uv, GDB, strace and
process tools. The connection travels
through `kubectl exec`; you do not need a pod IP or an exposed SSH service.

## Inspect the IOC in GDB

In the seat:

```bash
podbench debug
```

Use the arrow keys to select the IOC executable and press Enter. Select the IOC
process itself, rather than its shell, `stdio-socket`, or hotfix supervisor.
The process stops when GDB attaches. At the GDB prompt:

```text
info threads
thread apply all bt
continue
```

You should see the IOC's threads and their stacks. `continue` lets it run again;
Ctrl+C interrupts it for another inspection. Finish with:

```text
detach
quit
```

Detaching resumes the application. Exit the SSH shell when finished.
Source-level inspection needs an image with matching debug symbols and source.
For example, the PMAC IOC uses an `ioc-pmac-developer` image.
If you see only addresses, see [troubleshooting](../how-to/troubleshooting.md).

## Use VS Code

Back on your workstation:

```bash
podbench ide vscode bl47p-mo-ioc-01-0
```

Accept workspace trust if appropriate for the displayed seat and checkout. Wait
for Remote-SSH and the remote extensions to finish loading, then open **Run and
Debug**.

:::{important}
Select the launcher named **`Podbench: Attach — …`** for the IOC executable.
Use the generated Podbench launcher: it resolves the current process and supplies
the filesystem mappings and debugger setup needed for this seat. Check the
`Podbench` prefix before pressing F5.
:::

Start that launcher, pause briefly, and inspect **Call Stack**, **Threads** and
**Variables**. Resume with F5; use **Stop Debugging** to disconnect when done.
After an application restart, start the same Attach configuration again; it
resolves the replacement process without regenerating the workspace.
If Kubernetes replaces the pod, rerun `podbench ide vscode` from your workstation
to connect to a seat in the replacement pod.

By default, IDE launch adds 4 CPU / 8 GiB of limit headroom and 2 CPU / 4 GiB of
requests to the live pod (Guaranteed pods reserve the full limit). It requires
in-place resize support and permission. Reconnecting does not add it again.
If you have sufficient existing resources, use `--no-headroom` to skip resizing.

## Check the session is finished

```bash
podbench status
kubectl get pod bl47p-mo-ioc-01-0
```

The application should be running. Closing SSH or VS Code leaves the seat
available to reconnect; Kubernetes removes ephemeral containers when the pod is
replaced. Do not delete the application pod merely to tidy up a seat.

Plain attach leaves an ordinary workload's liveness probes unchanged, so a long
debugger pause can cause Kubernetes to restart the application. The host's ptrace
restrictions also apply, as described in the warning above. Use the
[hotfix workflow](hotfix.md) for probe protection and source editing.
