# Edit and debug an application with hotfix

Edit the persistent checkout, restart normally, then use VS Code Launch for
startup breakpoints or Attach to inspect the running process. Complete
[setup](setup.md) and arrange an interruption window with the application owner.

This example uses BlueAPI on P47: pod `p47-blueapi-0`, container `blueapi`.
Substitute your application's pod, container and source repository.

## Check the checkout and lifecycle wiring

On your workstation:

```bash
podbench status
```

Find `p47-blueapi-0` / `blueapi`. Its hotfix state should include `initialized`
and `normal`. **Do not initialize an existing checkout again.** `legacy wiring`
means the supervisor needs updating and a pod rollout; follow
[prepare a workload](../how-to/enable-hotfix.md). `ready for init` means the claim
still needs initialization.

```bash
podbench ide vscode p47-blueapi-0
```

Opening the IDE leaves BlueAPI running. In the seat's terminal:

```bash
cd /podbench/app
git status --short
git diff
```

Keep existing edits. The source on the claim can differ from the image version.

## Edit and run normally

Open `/podbench/app/src/blueapi/service/main.py`. Find `health_probe` and add a
logging line just before its return:

```python
def health_probe() -> HealthProbeResponse:
    """If able to serve this, server is live and ready for requests."""
    LOGGER.info("Podbench hotfix tutorial: health probe reached")
    return HealthProbeResponse(status=Health.OK)
```

Save, then run in the configured seat:

```bash
podbench restart
curl -fsS http://127.0.0.1:8000/healthz
```

Restart reruns the full startup command and waits for health checks. Its spinner
may remain visible while BlueAPI initializes devices. Check the new log message
from your workstation:

```bash
kubectl logs p47-blueapi-0 -c blueapi --tail=40
```

The health request does not execute a plan or move hardware. Ordinary Python
source edits need no reinstall. For dependency changes, use workstation
`podbench hotfix restart p47-blueapi-0 --container blueapi --reinstall`; see
[restart and recover](../how-to/restart-and-recover.md).

## Launch under the debugger

In the seat terminal, stop the application deliberately:

```bash
podbench stop
```

Alternatively, run the generated **Podbench: Stop** task. Supported liveness
probes stay held; readiness remains active and can mark BlueAPI unavailable.

Open **Run and Debug** and select **Podbench: Launch — …** for the BlueAPI server
invocation. Use the generated Podbench configuration, not a launcher supplied by
the application repository. Launch refuses while the application is running.

Set a breakpoint on a startup line in the BlueAPI source and press F5. Launch
runs the captured server invocation inside the application container, with its
original interpreter, environment and working directory. It does not rerun the
parent startup script; use Start for that.

Inspect **Call Stack** and **Variables**, then continue. You can also break on
your new health logging line and request `/healthz` from the seat terminal.
Resume promptly because real clients and readiness checks depend on the server.

Edit and save a line, then use VS Code's **Restart** debugging control to launch
again. The workspace and seat stay connected; no PID refresh is needed. **Stop
Debugging**, process exit, or loss of the launch transport leaves the application
stopped and held. Restore normal execution with:

```bash
podbench start
```

The generated **Podbench: Start** task performs the same operation. If these
terminal commands are missing, the seat has an older Podbench package; see
[troubleshooting](../how-to/troubleshooting.md).

## Attach to the running process

Select **Podbench: Attach — …** for the server and press F5. Podbench resolves the
current matching process before each session, including after Stop/Start. You do
not need to reopen the workspace. No match or multiple matches produces an error.

Set a breakpoint on the logging line and call `/healthz`. Disconnect when done:
Attach leaves the server running and releases only its session's probe hold.
Injected debugpy may remain until the next application restart.

## Undo the tutorial change

End debugging and remove only the logging line you added. In the seat, run
`podbench start` if Launch left the application stopped, or `podbench restart`
if it is already running. From the workstation, check:

```bash
podbench status
kubectl get pod p47-blueapi-0
```

Confirm normal execution, no remaining hold, readiness and an unchanged
application-container restart count. The checkout remains on its PVC.

## Apply the same loop to an IOC

For the P47 PMAC IOC, use pod `bl47p-mo-ioc-01-0`, container `bl47p-mo-ioc-01`,
and source repository `https://github.com/epics-containers/ioc-pmac.git`.
Its hotfix entrypoint uses `/podbench/app/ioc/start.sh` when present.

```bash
podbench ide vscode bl47p-mo-ioc-01-0 --target bl47p-mo-ioc-01
```

Use the generated native **Podbench: Launch — …** or **Podbench: Attach — …**
configuration for the IOC executable. Start reruns the startup script; Launch
runs the selected executable under GDB in the application container. Native
source edits require the IOC's build procedure before launching the rebuilt
binary. Cloning `ioc-pmac` alone does not replace the image's support binaries.
