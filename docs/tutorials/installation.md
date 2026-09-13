# Set up for P47

By the end you will have the Podbench CLI, a working Kubernetes context, and SSH
configured for a debugging seat.

## Load the DLS environment

On a DLS workstation with environment modules:

```bash
module load ec/p47
module load uv
kubectl config current-context
kubectl config view --minify -o jsonpath='{.contexts[0].context.namespace}{"\n"}'
kubectl get pods
```

The namespace should be `p47-beamline`. **The `ec/p47` module sets the default
namespace**, so the commands in these guides do not need `--namespace` or `-n`.
Check the context before starting each session.

:::{admonition} Outside DLS
Install [uv](https://docs.astral.sh/uv/getting-started/installation/) and
[kubectl](https://kubernetes.io/docs/tasks/tools/), and obtain a kubeconfig from
your cluster administrator. Select your context with
`kubectl config use-context YOUR_CONTEXT`, then set its default namespace with
`kubectl config set-context --current --namespace=YOUR_NAMESPACE`.
Alternatively, add `-n YOUR_NAMESPACE` to each workstation Podbench and kubectl
command. Substitute your own pod, container and source repository below.
:::

If the API cannot be reached from home, follow [remote work](../how-to/remote-work.md)
first. Run Podbench on the same workstation as your VS Code CLI.

## Install Podbench and check SSH

```bash
uv tool install git+https://github.com/epics-containers/podbench@main
podbench --help
```

If the command is not on your path, run `uv tool update-shell` and open a new
terminal. You need Git and OpenSSH locally. Podbench uses
`~/.ssh/id_ed25519` and its `.pub` file by default. If you do not already have
that key pair, create it with `ssh-keygen -t ed25519`; use `--identity PATH` on
attach or IDE commands to select another key.

```bash
podbench doctor --fix
```

This checks local tools and cluster access, and installs Podbench's SSH `Include`
in your SSH configuration. It does not grant cluster permissions. Resolve failed
checks before continuing; see [troubleshooting](../how-to/troubleshooting.md).

For the graphical tutorials, install VS Code and its **Remote - SSH** extension,
and check that `code --version` works. Podbench installs the remote Python and
C/C++ debugging extensions when opening the seat.

## Identify the examples

```bash
kubectl get pod bl47p-mo-ioc-01-0 p47-blueapi-0
kubectl get pod bl47p-mo-ioc-01-0 p47-blueapi-0 \
  -o custom-columns='POD:.metadata.name,CONTAINERS:.spec.containers[*].name'
```

| Application | Pod | Target container |
| --- | --- | --- |
| PMAC IOC | `bl47p-mo-ioc-01-0` | `bl47p-mo-ioc-01` |
| BlueAPI | `p47-blueapi-0` | `blueapi` |

These names were checked on P47 in September 2026. If a name has changed, select
its current replacement from `kubectl get pods`. Choose the BlueAPI application
pod, not its separate OAuth2 proxy.

Continue with [attach](attach.md) or [hotfix](hotfix.md). Both operate on the live
application: arrange a session with the beamline team before pausing or restarting it.
