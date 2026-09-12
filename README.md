# podbench prototype

Podbench keeps two modes and a single seat shape:

- podbench attach POD lands an ephemeral container using the target
  container's UID, GID and seccomp profile when Kubernetes reports them.
  Non-root seats drop all capabilities; root seats request four SSH capabilities.
- podbench hotfix manages a source checkout on one persistent claim per
  single-replica workload.

The image contains a shell, Git, uv, gdb, strace and basic process tools.
Attach prints an SSH command carried by `kubectl exec` (no pod network).
Use `--verbose` to also print a raw exec fallback. Images pull by default.
Inside a seat, `podbench debug` shows the process tree and attaches GDB to the
selected process using its container filesystem. `podbench debug --python PID`
instead uses GDB to inject debugpy on port 5678; restart the hotfix child after
disconnecting to remove the injected debugger. From your workstation, run
`kubectl port-forward -n NAMESPACE pod/POD 5678:5678` and connect your IDE's
Python attach configuration to `127.0.0.1:5678`. Exec liveness probes remain
held during the debug session; restart clears the hold.
`podbench status` shows attached seats and hotfix state together.
Main publishes `ghcr.io/epics-containers/podbench:prototype-attach-hotfix`;
override that with `--image` or `PODBENCH_IMAGE`. Use `attach --new` to pick up
a rebuilt image when a seat already exists.
Run `podbench doctor` to check local and cluster prerequisites; `--fix` only
creates the SSH config directory and installs that Include safely.

## Get connected

Install the workstation CLI from main:

    uv tool install git+https://github.com/epics-containers/podbench@main

You need `kubectl` configured for your cluster and an SSH key pair (by default `~/.ssh/id_ed25519`;
use `--identity` to select another).

    podbench doctor --fix -n NAMESPACE
    podbench attach POD --target CONTAINER -n NAMESPACE

Run the printed SSH command, or select its host alias in VS Code Remote-SSH.
Aliases pin the context and kubeconfig paths used at attach time.
`--target` defaults to the pod's first container. Hotfix commands call the same
option `--container`; use it consistently for pods with multiple containers.

## VS Code

    podbench ide vscode POD -n NAMESPACE [--target CONTAINER]

Run on your workstation with `code`, `kubectl`, SSH and a loaded SSH agent.
The command opens a normal Remote-SSH workspace, installs Python/C++ debug
extensions in the seat, and generates an attach launcher for each readable
application process. Hotfix code at `/podbench/app` is already in the workspace;
generated files stay in the seat's home, outside your checkout. Rerun after an
application restart to refresh PIDs. Python is injected only when you start its
debug launcher, copied from the seat image (or the hotfix environment) into the
target's `/tmp`; restart the hotfix child afterwards to remove the debugger.
Normal ptrace/Yama restrictions still apply.

The live pod gains **4 CPU / 8 GiB of limit headroom**, with **2 CPU / 4 GiB
of additional requests** (Guaranteed pods reserve the full limit). Reconnecting
does not repeatedly increase these values. This requires in-place resize support,
`patch pods/resize`, `patch pods`, and `list limitranges`; launch waits for actual
allocation and stops if the cluster cannot provide it. Only controller-owned
pods are supported: Argo CD keeps reconciling the unchanged workload template,
while the extra budget lasts until the pod is replaced. No Argo settings change.

Your SSH agent is forwarded and Git name/email are copied to the seat; private
keys and credential helpers stay local. Use an SSH Git remote to push. Forwarding
lasts while the SSH connection is alive, and any process in the pod that runs as
the seat's UID can use the agent during that time. The command installs Podbench's SSH
Include if needed; VS Code must use your normal `~/.ssh/config`.

## Hotfix lifecycle

1. Enable hotfix wiring in an epics-containers service chart, then review and
   deploy the Git diff:

       podbench hotfix enable SERVICE_DIRECTORY -n NAMESPACE

   This requires a live workload and derives the release, pod, container,
   security identity, entrypoint and values layout. IOC and services-template
   BlueAPI charts are wired automatically; options override those defaults.

2. For other charts, print the dependency and values for manual application:

       podbench hotfix values --app RELEASE --from-pod POD -n NAMESPACE

   For wrapper charts, use `--values-prefix KEY` to nest the workload settings
   under their chart key. The claim settings remain at the top level. Use
   `--entrypoint` to select code under `/podbench/app` when the original command
   still points to the image's installed application.

3. Initialize its claim:

       podbench hotfix init POD --repo URL -n NAMESPACE

   Python projects are synced with uv and gain debugpy for on-demand injection.
   Other repositories are cloned without a dependency-install step. The claim
   directory must be empty apart from `lost+found`: init refuses to replace
   a checkout. After a failed init, inspect and back up its contents before
   clearing the directory and retrying.

   Generated values keep liveness probes but extend non-exec probe failure
   thresholds for the two-minute restart window.

4. Edit /podbench/app in the seat and relaunch:

       podbench hotfix restart POD -n NAMESPACE
       podbench hotfix status -n NAMESPACE

   Add `--reinstall` to restart after changing Python dependencies. This requires
   a running seat attached to the same application container.

5. Remove the generated workload values, redeploy, then retire the PVC:

       podbench hotfix retire PVC --delete-claim -n NAMESPACE

This is a rapid-iteration prototype. Root and unknown-identity targets use the
same seat spec and may not provide useful ptrace access.
Hotfix initialization also installs a small `LD_PRELOAD` shim so dynamically
linked application processes opt into Yama debugging without `SYS_PTRACE`.
