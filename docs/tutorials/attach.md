# Debug a running IOC with attach

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

Inspect P47's PMAC IOC without replacing its application container. You will
attach a seat, read an IOC backtrace, and open the same application in VS Code.
Complete [setup](setup.md) first.

## Attach a seat

On your workstation:

```bash
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
Source-level inspection needs an image with matching debug symbols and source;
P47's PMAC IOC used the `ioc-pmac-developer` image when this guide was checked.
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
Select the launcher named **`Podbench C/C++ PID: …`** for the IOC executable.
**Launchers supplied by the target repository will not work.** Podbench's
launchers contain the process IDs, filesystem mappings and debugger setup needed
for this seat. Check the `Podbench` prefix before pressing F5.
:::

Start that launcher, pause briefly, and inspect **Call Stack**, **Threads** and
**Variables**. Resume with F5; use **Stop Debugging** to disconnect when done.
After an application restart, rerun `podbench ide vscode …` to regenerate launchers
for the new PIDs.

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

On hotfix-wired workloads, Podbench holds supported exec probes during debugging.
Plain attach does not retrofit probe protection into an ordinary workload, so
long pauses can trigger its existing liveness probes. Ptrace restrictions also
still apply; see [attach and hotfix explained](../explanations.md).

Next, use [hotfix](hotfix.md) to change source and debug the result.
