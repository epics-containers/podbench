# Edit and debug an application with hotfix

Make a small, reversible source change, restart the application from its
persistent checkout, and inspect the change in VS Code. Complete [setup](setup.md)
and arrange a restart window with the team responsible for the application first.

The worked example is BlueAPI on P47, pod `p47-blueapi-0`, container `blueapi`.
Use your own pod, container and source repository for another deployment. The
example code change is specific to BlueAPI; the edit, restart and debug loop
applies to other applications too.

## Check the hotfix checkout

On your workstation:

```bash
podbench hotfix status
```

Find your application in the output. For the example, this is
`p47-blueapi-0/blueapi`, with claim `p47-blueapi-podbench-project`. It should report
`initialized`; **do not initialize an existing checkout again**. If it is
missing or says `ready for init`, follow [prepare a workload](../how-to/enable-hotfix.md)
and return here.

```bash
podbench ide vscode p47-blueapi-0 --target blueapi
```

The generated workspace includes `/podbench/app`, the persistent BlueAPI source
checkout. In the **seat's VS Code terminal**, inspect it:

```bash
cd /podbench/app
git status --short
git diff
```

Keep any existing edits. The checkout can differ from the version in the image;
use the source actually running from the claim.

## Make a visible change

Open `/podbench/app/src/blueapi/service/main.py`. Find `health_probe` and add a
single logging line immediately before its return:

```python
def health_probe() -> HealthProbeResponse:
    """If able to serve this, server is live and ready for requests."""
    LOGGER.info("Podbench hotfix tutorial: health probe reached")
    return HealthProbeResponse(status=Health.OK)
```

Save the file. On your **workstation**, restart the application child:

```bash
podbench hotfix restart p47-blueapi-0 --container blueapi
kubectl logs p47-blueapi-0 -c blueapi --tail=40
```

The restart command waits for the child to change and checks startup. You should
see the new log message as health probes arrive. If necessary, request one from
the **seat terminal**:

```bash
curl -fsS http://127.0.0.1:8000/healthz
```

This only calls the health endpoint; it does not run a plan or move hardware.
Changes to Python dependencies also need `--reinstall`; see
[restart and recover](../how-to/restart-and-recover.md).

## Debug the edited code

Rerun on your workstation to refresh the process IDs:

```bash
podbench ide vscode p47-blueapi-0 --target blueapi
```

Open **Run and Debug** in the generated workspace.

:::{important}
Select **`Podbench Python PID: …`** for the BlueAPI server process.
**The debug launchers in the BlueAPI repository will not work.** Use the launcher
with the `Podbench` prefix: it prepares debugpy in the running process and supplies
the connection and path mappings. Choose the server, not a worker subprocess.
:::

Set a breakpoint on your new logging line and start the selected launcher with
F5. A health probe should hit it; you can also run the curl command above in the
seat. Inspect **Call Stack** and **Variables**, then resume promptly. Readiness
probes still run while debugging, so leaving this endpoint paused can make
BlueAPI temporarily unready.

If the breakpoint is unbound, check that you opened the file under
`/podbench/app` and selected the current server PID. The generated launcher injects
Python debugging only when you start it.

## Undo the tutorial change

Stop debugging. Remove only the logging line you added and save the file; avoid
resetting somebody else's changes. On your workstation:

```bash
podbench hotfix restart p47-blueapi-0 --container blueapi
podbench hotfix status
kubectl get pod p47-blueapi-0
```

Restarting removes the injected debugger and clears the probe hold. Confirm there
is no `HELD` state and the application is ready. The checkout remains on its PVC
for the next session. Rerun the IDE command before debugging the new process.

## Apply the same loop to an IOC

For an IOC example, the PMAC IOC on P47 uses pod `bl47p-mo-ioc-01-0`, container
`bl47p-mo-ioc-01`, and source repository `https://github.com/epics-containers/ioc-pmac.git`. Its hotfix entrypoint
uses `/podbench/app/ioc/start.sh` when present. Open its seat and inspect the
checkout, then edit the startup script for a controlled startup change:

```bash
podbench ide vscode bl47p-mo-ioc-01-0 --target bl47p-mo-ioc-01
podbench hotfix restart bl47p-mo-ioc-01-0 --container bl47p-mo-ioc-01
```

Use its **Podbench C/C++** launcher for native debugging. Restart does not compile
C/C++: native changes need the IOC's build procedure and an entrypoint that runs
the rebuilt binary. Cloning `ioc-pmac` alone does not replace the image's EPICS
support binaries. See [attach](attach.md) for inspecting the running IOC.
