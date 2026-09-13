# Attach validation

Inspect `podbench attach --help` first. In the trimmed CLI, the workload argument is a pod name and the target-container option is `--target`; do not substitute a deployment name or `--container` without checking that the interface has changed.

Use an explicit image and pull policy when validating freshly built behavior. Confirm whether the command creates or reuses a seat, then use the `--verbose` exec fallback non-interactively for repeatable checks. Validate:

- the seat image/version is the one requested;
- the seat targets the intended application container;
- the target process appears in the shared PID namespace;
- expected shared mounts are visible; and
- the application remains Ready with an unchanged restart count.

Test reuse by repeating the same request, including alternating between targets in a multi-container pod. Reuse must match the target, image and requested UID/GID. Request a different image or identity and verify a compatible seat is selected or created. Use `--new` to exercise a rebuilt floating image; pulling does not update a running seat.

Ephemeral containers cannot be removed from a pod. Use a disposable workload or plan a workload rollout for cleanup. A capless seat may see the target process while being unable to ptrace it or traverse protected paths such as `/proc/1/root`; record that as a runtime-security limitation unless the requested contract promises stronger access.

An attach test proves the seat and printed entry command. It does not prove SSH or VS Code Remote-SSH. For those, also verify the SSH server, authentication/forwarding route, stable connection target, and a real remote shell before calling the experience seamless.

For the SSH path, run `podbench doctor --fix` first and confirm the generated config Include is active. Use the alias printed by attach (currently `podbench.NAMESPACE.POD.SEAT.CONNECTION`) backed by a strict per-seat host key and a `kubectl exec` ProxyCommand; it does not require pod networking. Verify both `ssh ALIAS` and stock VS Code Remote-SSH against that alias. On root-target seats, confirm the runtime capability probe finds `SETGID`, `SETUID`, `SYS_CHROOT`, and `AUDIT_WRITE`; attach must report SSH as unavailable when any are missing rather than advertising a broken route.

For reconnect checks, confirm the generated ProxyCommand pins the context and absolute kubeconfig paths. Exercise matching pod names in two contexts, then change the workstation current context and reconnect from a shell without the original KUBECONFIG environment. The two generated aliases/config files must remain distinct and reach their original targets. `ssh -G` checks configuration parsing only; it does not prove an actual connection.
