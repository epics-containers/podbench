# Troubleshoot a debugging session

Use these checks for any debugging session. Commands that name a pod use BlueAPI
on P47 as an example; substitute your own pod and container.

Start on your workstation:

```bash
podbench doctor
podbench status
kubectl get pods
```

| Symptom | What to check |
| --- | --- |
| Wrong pods or no pods | Load your beamline module (for example, `module load ec/p47`) and inspect the current context and namespace. For a tunnel, export its kubeconfig instead. |
| API timeout from home | Recreate the tunnel and check credential expiry; see [remote work](remote-work.md). |
| SSH Include missing | Run `podbench doctor --fix`. VS Code must use your normal `~/.ssh/config`. |
| `Forbidden` | Use the verb and resource in the error to request access. `doctor --fix` cannot grant RBAC. |
| IDE resize fails | The cluster needs in-place resize, `patch pods/resize`, `patch pods` and `list limitranges`, plus enough resources. Use `--no-headroom` only if the pod already has room. |
| Debugger fails or starts another application | Choose the generated **Podbench: Attach — …** or **Podbench: Launch — …** entry in Run and Debug; it supplies the seat's filesystem mappings and debugger setup. |
| Process exited / PID changed | Retry the same Attach configuration; it resolves the current process. If there is no match, Start the application. If ambiguous, identify the duplicate invocation. |
| Pod evicted during `ide vscode` | The seat's VS Code server (about 1.5 GiB) is charged to the pod's `ephemeral-storage` limit together with disk-backed `emptyDir` volumes, and that limit cannot be resized in place. Raise the target container's `limits.ephemeral-storage` in the service values; `ide vscode` warns before adding a seat when the budget looks too small. |
| Pod replaced | Rerun `podbench ide vscode POD` from your workstation to connect to a seat in the replacement pod. Retrying Attach in the old workspace is not enough. |
| Python breakpoint is unbound | Check the selected server invocation and source path. Use `/podbench/app` for hotfix source, or the target filesystem shown in the generated workspace for image source. |
| GDB shows addresses without source | Use matching debug symbols and source. An IOC developer image helps; a runtime image may have stripped them. |
| `ptrace: Operation not permitted` | Check target UID, seccomp and Yama policy. Attach cannot bypass them; hotfix initialization supplies a debugging shim for dynamically linked application processes after restart. |
| Hotfix is `HELD` | After Launch, run Start; after failed startup, repair the cause then retry Start. See [recovery](restart-and-recover.md). |
| Init says claim is not empty | Inspect existing work or a partial init. Do not erase it to make init succeed. |

## Supervisor wiring and seat package are separate

`initialized, normal` confirms the application supervisor is running. It does not
mean the seat's installed CLI is current. If `podbench --help` inside the seat has
no Start/Stop/Restart commands, use a seat image containing the new package or
install the matching wheel in a writable environment in the seat.

The workstation IDE command uploads its generated helpers, so its Start/Stop
tasks can be newer than the terminal CLI. An old supervisor still needs updated
chart wiring and a rollout; a new seat alone cannot upgrade it. Never re-init an
existing checkout to fix a version mismatch.

## Pick up a rebuilt seat image

```bash
podbench attach p47-blueapi-0 --new
```

Attach normally reuses a running seat. `--new` lands another one using the current
image. Use `--image IMAGE` or `PODBENCH_IMAGE` to select a different image; the
development image is `ghcr.io/epics-containers/podbench:prototype-attach-hotfix`.
IDE selects the newest running seat matching the target, image and identity; pass
the same `--image IMAGE` to `podbench ide vscode` when using a different tag.

For an SSH transport failure, run attach with `--verbose` to print its raw
`kubectl exec` fallback. The seat is reached through the API, not over the pod
network.
