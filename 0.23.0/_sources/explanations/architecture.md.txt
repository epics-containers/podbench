# Architecture overview

Podbench connects your workstation to a **debug seat** beside a running
application in Kubernetes. The seat supplies the development tools; the
application continues to run in its own container. With hotfix enabled, both
containers can access a persistent source checkout, and a supervisor controls
the application's lifecycle.

## Where everything runs

This diagram shows a workload prepared for hotfix. Plain attach uses the same
workstation and seat connection, but does not require the supervisor or source
claim.

```{graphviz}
:caption: Workstation, application pod and persistent hotfix storage.
:alt: The workstation connects through the Kubernetes API to a debug seat. In the same pod, the seat sends lifecycle requests to the application supervisor. The seat and application share a persistent source checkout.

digraph architecture {
  graph [rankdir=TB, bgcolor="white", pad=0.2, nodesep=0.5];
  node [shape=box, style="rounded,filled", fillcolor="#eef4fa",
        fontname="sans-serif", fontsize=12];
  edge [fontname="sans-serif", fontsize=10];

  workstation [label="Workstation\nPodbench CLI · kubectl · VS Code"];
  api [label="Kubernetes API"];

  subgraph cluster_pod {
    label="Application pod";
    fontname="sans-serif";
    style=rounded;
    seat [label="Debug seat\nSSH · VS Code Server · development tools"];
    subgraph cluster_application {
      label="Application container";
      style=rounded;
      supervisor [label="Hotfix supervisor"];
      application [label="Application process"];
    }
    seat -> supervisor [label="lifecycle requests"];
    supervisor -> application [label="start / stop / launch"];
  }

  source [label="Persistent volume claim\nSource checkout: /podbench/app",
          shape=cylinder, fillcolor="#f1f6e9"];
  workstation -> api [label="authenticated kubectl access"];
  api -> seat [label="SSH carried over kubectl exec"];
  seat -> source [label="edit / build"];
  application -> source [label="run hotfix code"];
}
```

**On your workstation**, the Podbench CLI uses your Kubernetes context to find
the application and create or reuse a seat. For VS Code, it also prepares SSH
configuration, uploads its helper code and opens a generated workspace.

**In the debug seat**, SSH serves the connection used by VS Code Remote - SSH.
VS Code Server, remote extensions, terminals and development tools run here.
The SSH connection travels through `kubectl exec`; it does not require an
exposed SSH service or a route from your workstation to the pod IP.

**In the application container**, the application keeps its own runtime and
environment. The seat targets this container so its tools can inspect the
application's processes. Debugger access still depends on process permissions
and the host's ptrace policy.

**On the persistent claim**, hotfix stores the source checkout at
`/podbench/app`. Edits made from the seat are available to the application
container. Python source changes generally need an application restart;
dependency changes need installation, and native source changes need a build.

## How hotfix controls the application

Preparing a workload adds the persistent claim and a supervisor to its chart.
The supervisor runs inside the application container and remains alive while
the application child is stopped or restarted.

The workstation commands send requests through Kubernetes exec. Commands and
VS Code tasks inside a configured seat reach the supervisor through its local
control files, without needing Kubernetes credentials in the seat.

| Operation | What the supervisor does |
| --- | --- |
| Start | Runs the full configured startup command and waits for health checks. |
| Stop | Stops the application child and keeps it stopped. |
| Restart | Stops the child, then starts it normally. |
| VS Code Launch | Runs the selected application invocation under the debugger after an explicit Stop. |

During an intentional stop or debugging session, Podbench holds supported
liveness probes so Kubernetes does not restart the container. Readiness checks
continue and can mark the application unavailable. The hold does not make a
paused application able to serve its clients.

## How the debugger reaches the application

Opening VS Code connects the editor and prepares configurations; it leaves the
application running. You choose when to begin debugging.

**Attach** finds the current process by its executable and command arguments
each time a session starts. A new PID after an application restart is therefore
expected. Native Attach uses GDB from the seat; Python Attach injects debugpy
into the application and connects the VS Code debugger to it. Ending Attach
leaves the application running and releases that session's probe hold.

**Launch** asks the supervisor to run the selected invocation in the application
container, with its captured environment and working directory. Native Launch
runs under GDB there; Python Launch uses debugpy. Launch replays the selected
process command, whereas Start runs the full startup script that may prepare
the environment and start other processes too.

Ending Launch leaves the application stopped. Run Start to restore normal
execution. Python Attach may leave debugpy loaded after disconnection; an
application restart removes it.

## What survives a restart or disconnection

| Event | What remains and what to do next |
| --- | --- |
| Application Stop/Start or Restart | The pod, supervisor, seat and checkout remain. The application gets a new PID; reuse the generated Attach configuration if its command is unchanged. |
| Closing VS Code or SSH | The seat and persistent checkout remain available. End debugging and restore normal execution before closing the editor. |
| Ending or losing a Launch session | The application is left stopped and held; run Start. |
| Pod replacement | The old seat and debugging session are lost. The replacement application starts normally using the retained hotfix checkout. Rerun `podbench ide vscode` to reconnect. |
| Deleting the hotfix claim | The persistent checkout is deleted. Save the work and remove hotfix wiring before retiring the claim. |

By default, opening the IDE also reserves resource headroom on the live pod.
That allocation lasts until pod replacement and is not added again on each
connection. It does not change the workload's chart or controller template.

For a worked example, follow [the hotfix tutorial](../tutorials/hotfix.md).
For cleanup and recovery commands, see
[Restart, recover and retire a hotfix](../how-to/restart-and-recover.md).
