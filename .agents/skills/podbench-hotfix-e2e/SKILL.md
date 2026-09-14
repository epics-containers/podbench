---
name: podbench-hotfix-e2e
description: >-
  Prove the podbench hotfix lifecycle end to end on live pods from inside the
  agent sandbox: stop, start, edit through the claim, restart, and a headless
  VS Code connection to the seat. Use when asked to test hotfix mode, verify a
  podbench release against a cluster, upgrade legacy hotfix wiring, or run an
  end-to-end check before merging a podbench change. Ask the user which pods
  to test before mutating anything.
---

# Podbench hotfix end-to-end

Treat the cluster as live. Record state before every mutation, verify the
externally visible result, and leave every pod running normally at the end.
Follow [podbench-live-testing](../podbench-live-testing/SKILL.md) for the
general rules; this skill is the sandbox-driven procedure with evidence
scripts.

## 1. Setup the user does outside the sandbox

Check each item and ask for the ones that are missing. None can be done from
inside the jail.

| Need | How the user provides it | How to check |
|---|---|---|
| Route to the cluster API | `allow-ip = <API IP>` in the sandbox config, then a new `claude` session | `kubectl version` fails with `connect: invalid argument` when the route is missing |
| kubectl | static binary on the cache volume: `curl -L https://dl.k8s.io/release/$(curl -Ls https://dl.k8s.io/release/stable.txt)/bin/linux/amd64/kubectl -o /cache/bin/kubectl && chmod +x /cache/bin/kubectl` | `command -v kubectl` |
| Scoped kubeconfig | a file under `k8s/` with `pods/exec` and ephemeral-container rights | `podbench doctor -n NAMESPACE` reports attach and hotfix RBAC allowed |
| Headless VS Code | `install-vscode-driver-deps.sh` from the claude-sandbox `vscode-headless` skill, run in `claude-sandbox shell` | `command -v Xvfb` and `/cache/vscode/bin/code` |
| GitHub tokens | one PAT per repository owner, parked so they coexist; see `gh-park` or issue 47 in claude-sandbox | `git push --dry-run` in each checkout |
| SSH identity | none needed; `ssh-keygen -t ed25519 -N '' -f ~/.ssh/id_ed25519` inside the jail, then `podbench doctor --fix` | `podbench doctor` shows no FAIL |

`podbench` itself runs as `uv run podbench` from the checkout. Export
`PODBENCH="uv run --quiet podbench"` for the scripts.

## 2. Ask which pods

List candidates with `podbench status -n NAMESPACE` and show the HOTFIX
column. Then ask the user to pick, and confirm each name against the list.
Do not guess from a partial name: the T11 motion IOC is `bl11t-mo-sim-01-0`,
not `bl11i-mo-ioc-01`.

For each chosen pod record: container name, current `HOTFIX` state, and the
edit that will make a visible change. See
[references/t11-beamline.md](references/t11-beamline.md) for the known
targets and their edits.

## 3. Legacy wiring

`podbench restart` answers "lifecycle protocol unavailable" for pods wired by
podbench before 0.21. Upgrade through GitOps, never by editing the live pod:

1. Clone the services repository onto `/cache` and run
   `podbench hotfix enable services/RELEASE --from-pod POD --container NAME -n NAMESPACE`.
   Since PR #265 the command replaces its own earlier output and reports
   `updated`.
2. Inspect the diff: the launch argument must name the image's original
   command, the liveness probe must carry one hold guard, and the file must
   still parse.
3. Commit on a branch, open a PR, and let the user merge. Argo rolls the pod
   once. Claim contents on the PVC survive.
4. Confirm the new pod has `/tmp/podbench-control/state` before continuing.

## 4. Lifecycle evidence

Run the script once per pod. It prints a PASS/FAIL line per check and exits
non-zero on any failure:

```sh
scripts/hotfix-cycle.sh POD -n NAMESPACE --container NAME \
    --edit 'sed -i "/anchor/a\\    LOGGER.info(\"MARKER\")" /podbench/app/src/x.py' \
    --expect MARKER
```

It proves, in order: stop leaves the app stopped and held; start returns to
normal; the edit lands in the claim; restart replaces the supervised child;
the container restart count is unchanged; the marker appears in the log.

## 5. IDE connection

```sh
scripts/ide-check.sh POD NAMESPACE
```

This opens the seat in headless VS Code through the claude-sandbox
`vscode-headless` skill, drives the workspace window, and prints the remote
terminal's answer: user, `IS_SANDBOX=1`, and a read-only root, which proves
the seat terminal is inside the jail. A screenshot lands in `tmp/`. The
script closes its VS Code on exit; pass `KEEP_VSCODE=1` to leave it open.

Known behaviour the script already handles, worth keeping if you drive VS
Code by hand:

- podbench opens a bootstrap window first, so select the workspace window,
  not the first one.
- The workspace window gains its `[SSH: podbench...]` title suffix a few
  seconds after podbench reports `Ready`; wait for the suffix before driving
  it, or a command runs against a local, disconnected window.
- On a fresh VS Code profile the chat box holds focus, so focus or create a
  terminal through the command palette, and use output-only markers because
  the typed command is also visible in the snapshot.
- Give each run its own `--user-data-dir` and DevTools port, so a stale
  window from a previous pod cannot capture the workspace.

The seat image's podbench may be older than the workstation's, so in-seat
`podbench restart` can be missing. Note the version the terminal prints.

## 6. Report

Give one table per pod with the state, child PID, hold and restart count at
each step, the marker evidence, and the IDE line. Separate product findings
from environment findings. Leave every pod in `normal` state, and close the
headless VS Code with the DevTools `Browser.close` call.
