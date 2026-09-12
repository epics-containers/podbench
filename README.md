# podbench prototype

This branch intentionally keeps two modes and a single seat shape:

- podbench attach POD lands a capless ephemeral container using the target
  container's UID, GID and seccomp profile when Kubernetes reports them.
- podbench hotfix manages a source checkout on one persistent claim per
  single-replica workload.

The image contains a shell, Git, uv, gdb, strace and basic process tools.
Attach prints an SSH command carried by `kubectl exec` (no pod network), the
`~/.ssh/config` Include line, and a raw exec fallback. Images pull by default.
Inside a seat, `podbench debug` shows the process tree and attaches GDB to the
selected process using its container filesystem. `podbench debug --python PID`
instead uses GDB to inject debugpy on port 5678; restart the hotfix child after
disconnecting to remove the injected debugger.
`podbench status` shows attached seats and hotfix state together.
Development builds use `ghcr.io/epics-containers/podbench:prototype-attach-hotfix`;
override that with `--image` or `PODBENCH_IMAGE`.
Run `podbench doctor` to check local and cluster prerequisites; `--fix` only
creates the SSH config directory and installs that Include safely.

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
   under their chart key. The claim settings remain at the top level.

3. Initialize its claim:

       podbench hotfix init POD --repo URL -n NAMESPACE

   Python projects are synced with uv and gain debugpy for on-demand injection.
   Other repositories are cloned without a dependency-install step.

   Generated values keep liveness probes but extend non-exec probe failure
   thresholds for the two-minute restart window.

4. Edit /podbench/app in the seat and relaunch:

       podbench hotfix restart POD -n NAMESPACE
       podbench hotfix status -n NAMESPACE

5. Remove the generated workload values, redeploy, then retire the PVC:

       podbench hotfix retire PVC --delete-claim -n NAMESPACE

This is a rapid-iteration prototype. Root and unknown-identity targets use the
same seat spec and may not provide useful ptrace access.
Hotfix initialization also installs a small `LD_PRELOAD` shim so dynamically
linked application processes opt into Yama debugging without `SYS_PTRACE`.
