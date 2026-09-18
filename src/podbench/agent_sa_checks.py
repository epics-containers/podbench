"""Prove a freshly made agent credential is confined to its namespace.

Everything here runs as the NEW credential, never the caller's, except the
parent lookups that decide what a copied-permission account should hold.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from pathlib import Path

from .access import heading, kubectl, mark
from .agent_sa_rules import READ_VERBS, Tiers


class Mode(Enum):
    TIERS = "tiers"
    COPY = "copy"  # --all: the parent's permissions in the namespace
    READ = "read"  # the default: the parent's reads only


@dataclass(frozen=True)
class Expect:
    label: str
    args: tuple[str, ...]
    want: bool


def can_i(args: tuple[str, ...], kubeconfig: Path | None = None) -> str:
    """``yes``, ``no``, or ``""`` when the question itself failed.

    ``can-i`` exits 1 for a legitimate "no", so the exit code is not the answer.
    """
    result = kubectl("auth", "can-i", *args, kubeconfig=kubeconfig)
    answer = result.stdout.strip()
    return answer if answer in ("yes", "no") else ""


def inside(namespace: str, tiers: Tiers) -> list[Expect]:
    """The in-namespace questions, answered as the tiers say they should be.

    ``--subresource``, never ``pods/exec`` as one word: kubectl reads the slash
    as resource and resource *name*, so ``can-i create pods/exec`` asks about a
    pod called "exec", and ``can-i get pods/resize`` inherits ``get pods`` and
    says yes to a grant nobody holds.
    """
    ns = ("-n", namespace)

    def ask(label: str, want: bool, *args: str) -> Expect:
        return Expect(label, (*args, *ns), want)

    return [
        ask("read pods", True, "get", "pods"),
        ask("read deployments", True, "get", "deployments.apps"),
        ask("read limitranges", True, "get", "limitranges"),
        ask("read secrets", False, "get", "secrets"),
        ask("exec into pods", tiers.observe, "create", "pods", "--subresource=exec"),
        ask(
            "add ephemeral container",
            tiers.observe,
            "update",
            "pods",
            "--subresource=ephemeralcontainers",
        ),
        ask("create pods (iterate)", tiers.iterate, "create", "pods"),
        ask("patch services (iterate)", tiers.iterate, "patch", "services"),
        ask("delete pods", tiers.delete_pods, "delete", "pods"),
        ask(
            "write pods/resize (resize in place)",
            tiers.resize,
            "patch",
            "pods",
            "--subresource=resize",
        ),
        ask(
            "read pods/resize (kubectl GETs it first)",
            tiers.resize,
            "get",
            "pods",
            "--subresource=resize",
        ),
        ask("patch pods (hotfix)", tiers.hotfix, "patch", "pods"),
        ask(
            "patch deployments (hotfix: deploys code)",
            tiers.hotfix,
            "patch",
            "deployments.apps",
        ),
    ]


def outside(control: str) -> list[Expect]:
    ns = ("-n", control)
    return [
        Expect(f"read pods in {control}", ("get", "pods", *ns), False),
        Expect(f"read secrets in {control}", ("get", "secrets", *ns), False),
        Expect(
            f"exec into pods in {control}",
            ("create", "pods", "--subresource=exec", *ns),
            False,
        ),
        Expect("anything, anywhere", ("*", "*", "--all-namespaces"), False),
    ]


def _wanted(expect: Expect, mode: Mode) -> str:
    """A copied account follows its parent inside the namespace.

    Read-only copies deny every non-read verb even where the parent has it.
    """
    if mode is Mode.TIERS:
        return "yes" if expect.want else "no"
    if mode is Mode.READ and expect.args[0] not in READ_VERBS:
        return "no"
    return can_i(expect.args)


def _check(expect: Expect, want: str, config: Path) -> bool:
    if not want:
        mark("fail", f"parent permission check failed: {expect.label}")
        return False
    got = can_i(expect.args, config)
    if got == want:
        mark("ok", expect.label, got)
        return True
    mark("fail", expect.label, f"{got or 'error'} (wanted {want})")
    return False


def _refused(config: Path, label: str, *args: str) -> bool:
    """One real request, in case a webhook authorizer disagrees with can-i."""
    if kubectl(*args, kubeconfig=config).returncode == 0:
        mark("fail", f"live {label} succeeded")
        return False
    mark("ok", f"live {label} refused")
    return True


def _resize_live(config: Path, namespace: str) -> bool:
    """The first request ``kubectl patch --subresource=resize`` makes, for real.

    Reading the subresource mutates nothing; the write half is never exercised
    because that would resize somebody's workload.
    """
    pods = kubectl(
        "get", "pods", "-o", "jsonpath={.items[0].metadata.name}", kubeconfig=config
    )
    pod = pods.stdout.strip() if pods.returncode == 0 else ""
    if not pod:
        mark("info", f"no pod in {namespace} to exercise pods/resize against")
        return True
    result = kubectl("get", f"pod/{pod}", "--subresource=resize", kubeconfig=config)
    if result.returncode == 0:
        mark("ok", f"live read of pods/resize on {pod}")
        return True
    error = result.stderr.strip()
    if "forbidden" in error.lower():
        mark("fail", f"live read of pods/resize on {pod}", error)
        return False
    # An old kubectl, or a pod that went away, is no evidence about the grant.
    mark("info", "pods/resize not exercised", error)
    return True


def verify(config: Path, namespace: str, tiers: Tiers, mode: Mode) -> bool:
    control = "kube-system" if namespace == "default" else "default"
    title = {
        Mode.TIERS: "confinement checks",
        Mode.COPY: "sampled parent-permission and namespace-boundary checks",
        Mode.READ: "sampled read-only parent-permission and namespace-boundary checks",
    }[mode]
    heading(f"{title} (as the new account)")
    print(f"    inside {namespace}:")
    ok = True
    for expect in inside(namespace, tiers):
        ok &= _check(expect, _wanted(expect, mode), config)
    print(f"    outside {namespace} (control: {control}):")
    for expect in outside(control):
        ok &= _check(expect, "yes" if expect.want else "no", config)
    ok &= _refused(config, f"read of {control}/pods", "get", "pods", "-n", control)
    ok &= _refused(config, "cluster-wide pod list", "get", "pods", "--all-namespaces")
    if tiers.resize:
        ok &= _resize_live(config, namespace)
    return ok


INHERITED = [
    ("can list namespaces", ("list", "namespaces")),
    ("can read nodes", ("get", "nodes")),
    ("can list persistentvolumes", ("list", "persistentvolumes")),
    (
        "can list customresourcedefinitions",
        ("list", "customresourcedefinitions.apiextensions.k8s.io"),
    ),
    ("can list storageclasses", ("list", "storageclasses.storage.k8s.io")),
]


def report_inherited(config: Path) -> None:
    """Cluster-scoped reads from pre-existing bindings, usually on
    system:authenticated. Reported, never failed: no Role can subtract them.
    """
    print()
    heading("cluster-scoped reads inherited from cluster policy (not granted here)")
    found = [label for label, args in INHERITED if can_i(args, config) == "yes"]
    for label in found:
        mark("info", label)
    if not found:
        print("  (none)")
        return
    print(
        "  These expose cluster inventory, not workload data outside the namespace.\n"
        "  To find what grants them, run with your own credential:\n"
        "    kubectl get clusterrolebindings -o json | jq -r '.items[]\n"
        '      | select(.subjects[]? | .name=="system:authenticated"\n'
        '                          or .name=="system:serviceaccounts")\n'
        '      | .metadata.name + "  ->  " + .roleRef.name\''
    )
