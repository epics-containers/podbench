# Attach validation

Inspect `podbench attach --help` first. In the trimmed CLI, the workload argument is a pod name and the target-container option is `--target`; do not substitute a deployment name or `--container` without checking that the interface has changed.

Use an explicit image and pull policy when validating freshly built behavior. Confirm whether the command creates or reuses a seat, then run the printed `kubectl exec` command non-interactively for repeatable checks. Validate:

- the seat image/version is the one requested;
- the seat targets the intended application container;
- the target process appears in the shared PID namespace;
- expected shared mounts are visible; and
- the application remains Ready with an unchanged restart count.

Test reuse by repeating the same request. When image-selection behavior is in scope, request a different image or `--new` and verify that a new seat is created rather than silently reusing an incompatible seat.

Ephemeral containers cannot be removed from a pod. Use a disposable workload or plan a workload rollout for cleanup. A capless seat may see the target process while being unable to ptrace it or traverse protected paths such as `/proc/1/root`; record that as a runtime-security limitation unless the requested contract promises stronger access.

An attach test proves the seat and printed entry command. It does not prove SSH or VS Code Remote-SSH. For those, also verify the SSH server, authentication/forwarding route, stable connection target, and a real remote shell before calling the experience seamless.

For the SSH path, run `podbench doctor --fix` first and confirm the generated config Include is active. Attach should then print a stable `podbench.NAMESPACE.POD.SEAT` alias backed by a strict per-seat host key and a `kubectl exec` ProxyCommand; it does not require pod networking. Verify both `ssh ALIAS` and stock VS Code Remote-SSH against that alias. On root-target seats, confirm the runtime capability probe finds `SETGID`, `SETUID`, `SYS_CHROOT`, and `AUDIT_WRITE`; attach must report SSH as unavailable when any are missing rather than advertising a broken route.

## IDE acceptance and debugging pitfalls

Run `podbench ide vscode POD -n NAMESPACE [--target CONTAINER]` from the desktop workstation with its `code`, kubeconfig, SSH key and loaded agent. A headless `code` stub can validate orchestration but cannot prove the desktop connection or extension activation. Record user-reported desktop results separately from agent-observed checks.

Verify the hotfix checkout is open in the workspace, the remote terminal can list the forwarded agent and Git identity, and an SSH Git remote can push. Exercise Python and C/C++ attach, pause, stack inspection, source breakpoints, continue and disconnect. Rerun after process restarts to refresh launchers; use hotfix restart after Python debugging to remove the injected adapter and release the hold.

For in-place resource growth, compare the controller template before/after, wait for allocated resources in pod status, and repeat the command to check that budgets do not increase again. A patch response alone does not prove node allocation. Ephemeral containers cannot declare their own resource budget; pod-only resize survives normal reconciliation of a controller template but ends when the pod is replaced. Do not generalize this to directly GitOps-managed Pods.

Install and list debugging extensions through the running remote server's CLI: the local CLI may report an extension as installed without installing it remotely. Standalone cpptools adapter checks may need the executable permission normally set by extension activation; report that distinction. Opening an empty bootstrap folder before installation, then the final workspace, lets the new extension host discover installed extensions.

GDB can canonicalize `/proc/PID/exe` or a sysroot-prefixed executable to a different binary in the seat. Stage a private copy of the target executable and set its sysroot and thread-library paths before attach. Check actual target symbols and stack frames, not only GDB's exit code. Python's injected debugpy paths must resolve in both mount namespaces.

Check debugpy listeners through proc socket tables without consuming the DAP connection; tolerate missing IPv6 tables. Use a Python fixture that executes functions repeatedly for pause/breakpoint checks: a long native sleep or single-line loop may not reach a Python trace event promptly. Treat a process disappearing during source-map generation as recoverable, and reject non-object settings JSON without overwriting it.
