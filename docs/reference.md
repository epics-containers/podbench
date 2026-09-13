# Reference

## Commands

Run workstation commands in the intended Kubernetes context. At DLS, your
beamline module sets the default namespace: for example, `module load ec/p47`
selects `p47-beamline`. Elsewhere use your context's default or add `-n NAMESPACE`.

| Command | Purpose |
| --- | --- |
| `podbench doctor [--fix]` | Check prerequisites; optionally install local SSH configuration |
| `podbench status` | Show seats, hotfix state and probe holds |
| `podbench attach POD --target CONTAINER` | Add or reuse a seat and print SSH connection details |
| `podbench ide vscode POD --target CONTAINER` | Open a seat with generated Podbench debug launchers |
| `podbench hotfix enable DIRECTORY` | Edit a local service chart for hotfix |
| `podbench hotfix values --app RELEASE --from-pod POD` | Print wiring for manual integration |
| `podbench hotfix init POD --repo URL [--ref REF]` | Initialize an empty source claim |
| `podbench hotfix restart POD [--reinstall]` | Restart the child, optionally syncing Python dependencies |
| `podbench hotfix retire pvc/CLAIM [--delete-claim]` | Check an unused claim and optionally delete it |

Attach and IDE use `--target`; hotfix commands use **`--container`**. Pass it
consistently for multi-container pods. Use `--help` on any command for all options.
`--context NAME` selects a context explicitly.

Inside a seat:

| Command | Purpose |
| --- | --- |
| `podbench debug` | Select a process interactively and attach GDB |
| `podbench debug PID` | Attach GDB to a known PID |
| `podbench debug --python PID` | Inject debugpy for a manual Python attach on port 5678 |

For manual Python attach, forward `pod/POD` port `5678:5678` with kubectl on your
workstation and connect your IDE to `127.0.0.1:5678`. This needs port-forward
permission. The generated VS Code workflow handles its connection separately and
does not need this manual forward. Restart the hotfix child after Python debugging.

See the [release notes](https://github.com/epics-containers/podbench/releases)
for published versions.
