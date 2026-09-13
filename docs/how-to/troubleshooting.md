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
| Debugger fails or starts another application | Choose **Podbench Python** or **Podbench C/C++** in Run and Debug. The target repository's launchers will not work. |
| Process exited / PID changed | Rerun `podbench ide vscode …` after a restart and select its regenerated launcher. |
| Python breakpoint is unbound | Check the selected server PID and source path. Use `/podbench/app` for hotfix source, or the target filesystem shown in the generated workspace for image source. |
| GDB shows addresses without source | Use matching debug symbols and source. An IOC developer image helps; a runtime image may have stripped them. |
| `ptrace: Operation not permitted` | Check target UID, seccomp and Yama policy. Attach cannot bypass them; hotfix initialization supplies a debugging shim for dynamically linked application processes after restart. |
| Hotfix is `HELD` | Stop debugging and complete a child restart; see [recovery](restart-and-recover.md). |
| Init says claim is not empty | Inspect existing work or a partial init. Do not erase it to make init succeed. |

## Pick up a rebuilt seat image

```bash
podbench attach p47-blueapi-0 --target blueapi --new
```

Attach normally reuses a running seat. `--new` lands another one using the current
image. Use `--image IMAGE` or `PODBENCH_IMAGE` to select a different image; the
prototype default is `ghcr.io/epics-containers/podbench:prototype-attach-hotfix`.

For an SSH transport failure, run attach with `--verbose` to print its raw
`kubectl exec` fallback. The seat is reached through the API, not over the pod
network.
