# Work remotely through an SSH tunnel

Use this when your laptop can SSH to a DLS workstation but cannot reach the
Kubernetes API directly. Podbench's SSH transport uses `kubectl exec`, so an API
tunnel is sufficient for attach and the generated VS Code launchers.

The commands use P47 as an example. Substitute your beamline module, namespace,
SSH host and kubeconfig path.

The scripts live in the [Podbench repository](https://github.com/epics-containers/podbench/tree/main/k8s),
not the installed CLI. Run them from a checkout on each machine that needs them.
The scripts require Bash, kubectl and OpenSSH; copying permissions with `--all`
also requires jq.

## Create a namespace credential on the DLS host

On a DLS host with your own cluster credential and permission to create
ServiceAccounts, Roles and RoleBindings:

```bash
module load ec/p47
git clone https://github.com/epics-containers/podbench.git
cd podbench
./k8s/make-agent-sa.sh p47-beamline --podbench --duration 24h
```

Use the output filename printed by the script. It creates a namespace-confined
account named `agent-USER` and a self-contained kubeconfig under `k8s/`.
`--podbench` grants the attach/exec tier. For VS Code with this tier, add
`--no-headroom`; the default IDE resize needs additional pod patch permissions.

If the session needs your existing write permissions, use `--all` instead of
`--podbench`. It copies your current resource permissions within this namespace,
including secrets if you can access them. It does not copy cluster-scoped access.
For narrower access, ask your administrator for the specific permissions reported
by Podbench. The script's historical tier names do not cover every current IDE
operation; `--podbench=resize` alone does not grant `patch pods`.

The token normally lasts for the requested duration, but the server may shorten
it; the script prints its actual expiry. Rerun with your own credential to renew.
Keep generated kubeconfigs private and out of Git.

## Start the tunnel on your laptop

Install Podbench, kubectl and SSH locally as described in
[setup](../tutorials/setup.md). In a local Podbench checkout, set the actual
SSH host and **absolute path printed on the DLS host**. For example, replace
`FEDID` and `DLS_HOST` in:

```bash
REMOTE_KUBECONFIG='FEDID@DLS_HOST:/home/FEDID/podbench/k8s/p47-beamline-agent-FEDID.kubeconfig'
./k8s/vpn-api-tunnel.sh "$REMOTE_KUBECONFIG" \
  --out "$PWD/k8s/p47-tunnel.kubeconfig"
export KUBECONFIG="$PWD/k8s/p47-tunnel.kubeconfig"
kubectl get pods
podbench doctor --fix
podbench ide vscode p47-blueapi-0 --no-headroom
```

The script reads the source over SSH and writes a local kubeconfig using a
loopback API address. It retains the namespace and verifies TLS using the original
API hostname and CA. The host holding the file is also the default tunnel host;
use `--ssh-host HOST` if the API must be reached through another machine.

For an existing local kubeconfig, the equivalent is:

```bash
./k8s/vpn-api-tunnel.sh k8s/p47-beamline-agent-FEDID.kubeconfig \
  --ssh-host FEDID@DLS_HOST --out "$PWD/k8s/p47-tunnel.kubeconfig"
```

Use the same `KUBECONFIG` when generating SSH aliases and opening VS Code.
Podbench pins aliases to the context and kubeconfig paths used at attach time.
After changing the file path or context, regenerate them with attach or IDE.

:::{admonition} Outside DLS
Use an SSH host that can reach your API server, and your own namespace and
kubeconfig. If credentials use a local authentication plugin, that plugin must
also be available on your laptop; a ServiceAccount kubeconfig avoids that dependency.
:::

## Reconnect and clean up

If the connection times out after a network change, stop and recreate the tunnel
using the **same source argument**:

```bash
./k8s/vpn-api-tunnel.sh "$REMOTE_KUBECONFIG" --stop
./k8s/vpn-api-tunnel.sh "$REMOTE_KUBECONFIG" \
  --out "$PWD/k8s/p47-tunnel.kubeconfig"
kubectl --request-timeout=10s get pods
```

An SSH master can still report that it is running while its API forwarding is
stalled. If a fresh tunnel also fails, check whether the DLS host can reach the
API and whether the token expired. `--local-port 16443` selects another local
port if needed.

When finished, close VS Code and SSH sessions, stop the tunnel, and `unset
KUBECONFIG` to restore normal kubeconfig discovery. To revoke the temporary account,
on the **DLS host using your own credential** run:

```bash
module load ec/p47
./k8s/delete-agent-sa.sh p47-beamline
```

Review its confirmation prompt. Deleting the account invalidates its tokens and
removes the source kubeconfig there; also remove the local tunnel kubeconfig when
no longer needed.
