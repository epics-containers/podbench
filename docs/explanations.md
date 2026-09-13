# Explanations

## Attach and hotfix

Podbench is a prototype for rapid iteration on running Kubernetes applications.

A **seat** is an ephemeral container in the application pod. It carries the tools
needed to inspect another container's processes and serves SSH over `kubectl
exec`. Attach uses the target container's reported UID, GID and seccomp profile;
it does not guarantee ptrace access for every workload.

| | Attach | Hotfix |
| --- | --- | --- |
| Purpose | Inspect the running application | Edit source and restart the application child |
| Workload preparation | Permission to add an ephemeral container and exec | Chart wiring, a supervisor and one claim per single-replica workload |
| Source | Target image filesystem, or an existing hotfix checkout | `/podbench/app` on a persistent claim |
| Lifetime | Seat ends when the pod is replaced | Checkout survives pod replacement until the claim is deleted |

Hotfix uses the same attach seat. Its supervisor restarts the child inside the
application container; this keeps the checkout available and avoids an image
build for each Python or startup-script change. Native code still needs a build.

VS Code opens a generated workspace in the seat. Its **Podbench** launchers know
the target process and its filesystem. Launchers from the application's own
repository assume a different environment and will not work here. Regenerate
Podbench launchers whenever the application PID changes.

Pausing a process affects its real clients. Hotfix wiring allows supported exec
liveness probes to be held during debugging; readiness may still report the
application unavailable. Python debugging injects debugpy into the process and
requires a child restart to remove it and release the hold. Ordinary attach does
not automatically protect an unprepared workload from its probes.

IDE resource headroom is applied to the live controller-owned pod, leaving its
workload template unchanged. It lasts until pod replacement, and repeated IDE
connections do not repeatedly increase the budget. `--no-headroom` uses the
existing allocation.
