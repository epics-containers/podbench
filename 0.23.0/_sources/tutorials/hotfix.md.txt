# Edit and debug an application with hotfix

In this tutorial you will open BlueAPI's persistent source checkout in VS Code,
make an edit, and restart BlueAPI to try it. In the same VS Code window, you will
then use breakpoints to inspect the application: **Launch** starts it under the
debugger, while **Attach** connects to the running process. Finally, you will
undo the tutorial edit and return BlueAPI to normal operation.

Complete [setup](setup.md) first and arrange an interruption window with the
application owner: stopping the server or pausing at a breakpoint interrupts
its service. The example uses BlueAPI on P47, with pod `p47-blueapi-0` and
container `blueapi`. Substitute your own pod, container and source repository
when working on another application.

## Check the checkout and lifecycle wiring

On your workstation:

```bash
module load ec/p47
podbench status
```

If this is the first time the workload has been used for hotfix, follow
[Prepare a workload for hotfix](../how-to/enable-hotfix.md) first. It walks you
through generating and deploying the chart changes, replacing the pod, and
initializing its persistent source checkout. If someone has already prepared
the workload, use the status output to see how far they got.

Look for `p47-blueapi-0` in the `POD` column and `blueapi` in `TARGET`. After the
chart changes have been deployed and the pod replaced, but before the checkout
is initialized, its row looks like this:

```text
POD             TARGET   SEAT  HOTFIX
p47-blueapi-0   blueapi  —     ready for init, normal
```

`ready for init` means you still need the **Initialize an empty claim** step in
the preparation guide. Once you have completed that step, run `podbench status`
again. You are looking for:

```text
POD             TARGET   SEAT  HOTFIX
p47-blueapi-0   blueapi  —     initialized, normal
```

`initialized` means the persistent checkout has been created; `normal` means the
application is running under the lifecycle supervisor. The `SEAT` column can be
empty at this point—you will create or reconnect to a seat when you open VS Code.

If the row instead contains `legacy wiring`, follow the preparation guide to
update the supervisor and roll out a replacement pod. An existing initialized
checkout survives that rollout and does not need initializing again.

On later visits, `initialized, normal` means you can go straight to opening
VS Code. Rerunning `hotfix enable` regenerates the local chart files and reports
`unchanged` when they already match; it does not deploy changes or restart the
pod. Rerunning `hotfix init` on an initialized claim is refused, preserving the
existing checkout.

## Open the checkout in VS Code

On your workstation, run:

```bash
podbench ide vscode p47-blueapi-0
```

VS Code connects to a debug seat beside the application. Opening it leaves
BlueAPI running. Open a terminal in that VS Code window and inspect the
persistent checkout:

```bash
cd /podbench/app
git status --short
git diff
```

If both Git commands produce no output, the checkout has no tracked changes or
untracked files. Otherwise, review the changes before adding your own: this
checkout may contain someone else's work and may be at a different revision
from the container image. Keep those existing edits throughout the tutorial.

The commands below say whether to run them in this **seat terminal** or on your
**workstation**. Keep both available.

## Edit and run normally

Open `/podbench/app/src/blueapi/service/main.py`. Find `health_probe` and add a
logging line just before its return:

```python
def health_probe() -> HealthProbeResponse:
    """If able to serve this, server is live and ready for requests."""
    LOGGER.info("Podbench hotfix tutorial: health probe reached")
    return HealthProbeResponse(status=Health.OK)
```

Save the file. In the seat terminal, restart BlueAPI to load the edited source:

```bash
podbench restart
```

This stops the application child, reruns its full startup command, and waits
for health checks. It keeps the pod and your VS Code connection in place. The
spinner may remain visible while BlueAPI initializes devices; wait for the
command to finish before continuing.

Now request the health endpoint from the seat terminal:

```bash
curl -fsS http://127.0.0.1:8000/healthz
```

You should receive a successful health response. On your workstation, inspect
the application log:

```bash
kubectl logs p47-blueapi-0 -c blueapi --tail=40
```

Look for a line containing:

```text
Podbench hotfix tutorial: health probe reached
```

It may appear several times because Kubernetes also calls the health endpoint.
Seeing this message confirms that BlueAPI is executing your edited checkout.
If it is missing, check that you saved the file and that the restart succeeded
before moving on.

This Python source edit needs no reinstall. If you change dependencies as well,
follow [restart and recover](../how-to/restart-and-recover.md) to reinstall them.

## Launch under the debugger

Use Launch when you want to debug startup or rerun the application after an
edit. First, stop the running application from the seat terminal:

```bash
podbench stop
```

The command should finish with the application stopped. To check, run
`podbench status` on your workstation. The `HOTFIX` column should now contain:

```text
initialized, stopped, HELD
```

`HELD` means Podbench is keeping the hold-aware liveness check satisfied so
Kubernetes does not restart the container while you debug. Readiness checks
continue, so the pod may show as unready while the application is stopped.

In VS Code:

1. Open **Run and Debug** and choose the **Podbench: Launch — …** entry whose
   command is the BlueAPI server. The text after the dash depends on how your
   deployment starts BlueAPI.
2. Set a breakpoint on the logging line you added. To investigate startup, you
   can also set one on a line that runs while the server starts.
3. Press **F5** to launch. If Podbench reports that the application is already
   running, run `podbench stop` in the seat terminal and try again.

Launch runs the selected server command inside the application container, using
its captured interpreter, environment and working directory. It runs that
command directly; `podbench start` reruns the full parent startup script.

Once the server is listening, request the health endpoint from the seat terminal:

```bash
curl -fsS http://127.0.0.1:8000/healthz
```

VS Code should pause on your logging line. A Kubernetes health check may reach
the breakpoint before your request does. Inspect **Call Stack** and **Variables**,
then press **F5** to continue. A request waiting on the breakpoint will remain
pending until you resume execution.

To try an edit during this session, change the log message and save the file,
then use VS Code's **Restart** debugging control. It launches the server again
with your saved changes; the workspace and seat stay connected.

When finished, choose **Stop Debugging**. Ending a Launch session leaves the
application stopped and held, including if the launched process exits or its
connection is lost. In the seat terminal, restore normal execution:

```bash
podbench start
```

Wait for startup to complete. Workstation `podbench status` should again show
`initialized, normal`, with no `HELD` marker.

You can also run **Podbench: Stop** and **Podbench: Start** through VS Code's
**Tasks: Run Task** command. If the terminal commands are missing, see
[troubleshooting](../how-to/troubleshooting.md) to update the seat's Podbench
package.

## Attach to the running process

Use Attach to inspect BlueAPI while it is already running. Wait for the previous
section's `podbench start` to complete before attaching.

The restarted server has a new PID. Each time you start a **Podbench: Attach**
session, its preparation task finds the current process by matching the
executable and command arguments in the same application container. It uses
that process's PID, so you can reuse the configuration after Stop/Start.

In VS Code:

1. In **Run and Debug**, choose **Podbench: Attach — …** for the BlueAPI server.
2. Keep a breakpoint on your logging line and press **F5** to attach.
3. Run the health request from the seat terminal:

   ```bash
   curl -fsS http://127.0.0.1:8000/healthz
   ```

Inspect the paused process as before, then continue and disconnect the debugger.
Ending an Attach session leaves the server running and releases that session's
probe hold. The injected Python debugger may remain loaded until the next
application restart.

If Podbench cannot find a matching process, confirm the application is running.
If the executable or command arguments have changed, rerun `podbench ide vscode`
from your workstation to regenerate the configurations. Reconnect after a pod
replacement too. If several processes match, resolve the ambiguity before
retrying; see [troubleshooting](../how-to/troubleshooting.md).

## Undo the tutorial change

End any debugging session, remove the logging line you added, and save the file.
Review your diff in the seat terminal to confirm that you have kept any edits
that were there before the tutorial:

```bash
git diff
```

If BlueAPI is running, load the restored source with `podbench restart` in the
seat terminal. If Launch left it stopped, use `podbench start` instead. Wait for
the command to finish, then check from your workstation:

```bash
podbench status
kubectl get pod p47-blueapi-0
```

The `HOTFIX` column should show `initialized, normal` without `HELD`. In the pod
listing, check that all containers are ready. Starting and stopping the
application child should not increase Kubernetes' container restart count.

You can now close VS Code. The checkout stays on the persistent claim, ready for
your next session; you do not need to initialize it again.

## Apply the same loop to an IOC

The same Stop → Launch → Start and Attach workflows apply to a native IOC.
For the P47 PMAC IOC, first follow the IOC example in
[Prepare a workload for hotfix](../how-to/enable-hotfix.md). It uses pod
`bl47p-mo-ioc-01-0`, container `bl47p-mo-ioc-01`, and the
[`ioc-pmac` source repository](https://github.com/epics-containers/ioc-pmac).

Then open its seat from your workstation:

```bash
podbench ide vscode bl47p-mo-ioc-01-0 --target bl47p-mo-ioc-01
```

Choose the generated **Podbench: Attach — …** entry for the IOC executable to
inspect the running IOC. To debug startup, run `podbench stop` in the seat and
choose its **Podbench: Launch — …** entry. This launches the executable under
GDB inside the application container. When you end Launch, run `podbench start`
to return to normal operation.

For this IOC, Start reruns the startup script, using
`/podbench/app/ioc/start.sh` when present. Launch runs the selected executable
directly, as in the BlueAPI example.

Native source changes also need a build: follow the IOC's build procedure and
ensure the launched executable is the rebuilt binary. Cloning `ioc-pmac` into
the checkout alone does not replace the support binaries supplied by the image.
