"""Status and retirement for workload-scoped hotfix claims."""

from __future__ import annotations

from .hotfix_core import (
    MANIFEST,
    HotfixError,
    claim_name,
    exec_target,
    load_json,
    resolve_target,
)
from .kubectl import Kubectl, KubectlError
from .launcher import LauncherError
from .model import HOTFIX_APP_PATH, HOTFIX_CLAIM_VOLUME, HOTFIX_HOLD_PATH, as_dict


def _containers(pod: dict) -> list[dict]:
    value = as_dict(pod.get("spec")).get("containers", [])
    return (
        [item for item in value if isinstance(item, dict)]
        if isinstance(value, list)
        else []
    )


def hotfix_containers(pod: dict) -> list[str]:
    names: list[str] = []
    for container in _containers(pod):
        mounts = container.get("volumeMounts", [])
        if not isinstance(mounts, list):
            continue
        if any(
            isinstance(mount, dict)
            and mount.get("name") == HOTFIX_CLAIM_VOLUME
            and mount.get("mountPath") == HOTFIX_APP_PATH
            for mount in mounts
        ):
            name = container.get("name")
            if isinstance(name, str):
                names.append(name)
    return names


def hotfix_state(kube: Kubectl, pod: dict, container: str) -> tuple[str, bool]:
    """Return a state label and whether inspection succeeded with no probe hold."""
    name = as_dict(pod.get("metadata")).get("name")
    if not isinstance(name, str):
        return "unreachable", False
    try:
        target, _ = resolve_target(kube, name, container)
        result = exec_target(
            kube,
            target,
            f"test -d {HOTFIX_APP_PATH} || exit 1; "
            f"if [ -f {MANIFEST} ]; then echo initialized; "
            "else echo 'ready for init'; fi; "
            "if [ -f /tmp/podbench-control/state ]; then "
            "cat /tmp/podbench-control/state; else echo 'legacy wiring'; fi; "
            f"if [ -e {HOTFIX_HOLD_PATH} ]; then echo HELD; fi",
        )
    except (HotfixError, LauncherError, KubectlError, ValueError) as error:
        return f"unavailable: {error}", False
    state = result.stdout.strip().splitlines()
    return ", ".join(state), "HELD" not in state


def status(kube: Kubectl) -> tuple[list[str], bool]:
    lines: list[str] = []
    healthy = True
    for pod in kube.list_pods():
        name = as_dict(pod.get("metadata")).get("name")
        if not isinstance(name, str):
            continue
        for container in hotfix_containers(pod):
            state, state_healthy = hotfix_state(kube, pod, container)
            healthy = healthy and state_healthy
            lines.append(f"{name}/{container}: {claim_name(pod) or '?'} ({state})")
    return lines or [f"no hotfixes in namespace {kube.namespace}"], healthy


def _mounting_pods(kube: Kubectl, claim: str) -> list[str]:
    mounted: list[str] = []
    for pod in kube.list_pods():
        volumes = as_dict(pod.get("spec")).get("volumes", [])
        if any(
            as_dict(as_dict(volume).get("persistentVolumeClaim")).get("claimName")
            == claim
            for volume in volumes
        ):
            name = as_dict(pod.get("metadata")).get("name")
            if isinstance(name, str):
                mounted.append(name)
    return mounted


def retire(
    kube: Kubectl, target_or_claim: str, *, delete_claim: bool = False
) -> tuple[list[str], bool]:
    """Retire an unmounted claim; return messages and whether retirement is complete."""
    pod_name = target_or_claim.removeprefix("pod/")
    probe = (
        None
        if target_or_claim.startswith("pvc/")
        else kube.run("get", "pod", pod_name, "-o", "json", "--ignore-not-found")
    )
    claim = target_or_claim.removeprefix("pvc/")
    if probe and probe.stdout.strip():
        pod = load_json(probe.stdout)
        claim = claim_name(pod) or ""
        if hotfix_containers(pod):
            return (
                [
                    f"{pod_name} is still wired for hotfix",
                    "remove the hotfix values and redeploy before deleting its claim",
                ],
                False,
            )
        if not claim:
            raise HotfixError("the pod is unwired; pass the PVC name to retire")
    if not claim:
        raise HotfixError("no PVC name was provided")
    holders = _mounting_pods(kube, claim)
    if holders:
        return ([f"PVC {claim} is still mounted by {', '.join(holders)}"], False)
    existing = kube.run("get", "pvc", claim, "-o", "name", "--ignore-not-found")
    if not existing.stdout.strip():
        return ([f"PVC {claim} is gone; retirement is complete"], True)
    if not delete_claim:
        return (
            [
                f"PVC {claim} is unmounted and ready to delete",
                f"run hotfix retire {claim} --delete-claim",
            ],
            False,
        )
    kube.run("delete", "pvc", claim)
    return ([f"deleted PVC {claim}"], True)
