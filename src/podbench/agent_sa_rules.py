"""The RBAC that ``podbench make-sa`` grants, as plain data."""

from __future__ import annotations

import getpass
import re
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

MANAGED_BY = {"app.kubernetes.io/managed-by": "podbench-k8s-script"}
LABEL_SELECTOR = "app.kubernetes.io/managed-by=podbench-k8s-script"
TIER_NAMES = ("observe", "iterate", "resize", "hotfix")
READ_VERBS = ("get", "list", "watch")

Rule = dict[str, Any]


def account_name(who: str | None = None) -> str:
    """``agent-<user>``, normalised to the RFC 1123 label a ServiceAccount needs.

    $USER is often unset in a devcontainer or CI shell, and the API server
    rejects a dotted or upper-case name without saying which field was wrong.
    """
    if who is None:
        try:
            who = getpass.getuser()
        except (KeyError, OSError):
            who = ""
    who = re.sub(r"[^a-z0-9-]", "-", who.lower()).strip("-") or "unknown"
    return f"agent-{who.removeprefix('agent-')}"


@dataclass(frozen=True)
class Tiers:
    observe: bool = False
    iterate: bool = False
    resize: bool = False
    hotfix: bool = False

    @property
    def any(self) -> bool:
        return self.observe

    @property
    def delete_pods(self) -> bool:
        """Iterate tears down its dev pod; hotfix replaces a controlled pod."""
        return self.iterate or self.hotfix

    def __str__(self) -> str:
        return ",".join(name for name in TIER_NAMES if getattr(self, name))


def parse_tiers(values: Iterable[str]) -> Tiers:
    """Every tier implies observe: each ends in an attach, which needs exec."""
    chosen: set[str] = set()
    for value in values:
        for tier in filter(None, value.split(",")):
            if tier == "all":
                chosen.update(TIER_NAMES)
            elif tier in TIER_NAMES:
                chosen.add(tier)
            else:
                raise ValueError(
                    f"unknown podbench tier {tier!r}; want one or more of: "
                    "observe,iterate,resize,hotfix,all"
                )
    if chosen:
        chosen.add("observe")
    return Tiers(**dict.fromkeys(chosen, True))


def _rule(group: str, resources: list[str], verbs: list[str]) -> Rule:
    return {"apiGroups": [group], "resources": resources, "verbs": verbs}


# `secrets` is deliberately absent: with it the account could read every other
# ServiceAccount token in the namespace and become them.
BASE_RULES = [
    _rule(
        "",
        [
            "pods",
            "pods/log",
            "services",
            "configmaps",
            "persistentvolumeclaims",
            "events",
            "endpoints",
            "limitranges",
        ],
        list(READ_VERBS),
    ),
    _rule(
        "apps",
        ["deployments", "statefulsets", "replicasets", "daemonsets"],
        list(READ_VERBS),
    ),
    _rule("batch", ["jobs", "cronjobs"], list(READ_VERBS)),
    _rule("metrics.k8s.io", ["pods"], ["get", "list"]),
]


def tier_rules(tiers: Tiers) -> list[Rule]:
    """Mirror Charts/podbench/templates/rbac.yaml, whose comments say why.

    Keep the two in step: ``doctor`` names the chart flag for a missing grant.
    """
    rules = list(BASE_RULES)
    if tiers.observe:
        # The seat is PUT onto pods/ephemeralcontainers; exec is the transport.
        rules += [
            _rule("", ["pods/ephemeralcontainers"], ["get", "patch", "update"]),
            _rule("", ["pods/exec"], ["create"]),
        ]
    if tiers.iterate:
        # A sacrificial dev pod, and the Service patch behind --take-traffic.
        rules += [
            _rule("", ["pods"], ["create", "delete"]),
            _rule("", ["services"], ["patch"]),
        ]
    if tiers.resize:
        # kubectl GETs the subresource before it patches it, so `patch` alone
        # is Forbidden on the read and no PATCH is ever sent.
        rules.append(_rule("", ["pods/resize"], ["get", "patch"]))
    if tiers.hotfix:
        # Annotating the pod template rolls the workload: this DEPLOYS CODE.
        rules += [
            _rule("apps", ["deployments", "statefulsets"], ["patch"]),
            _rule("", ["pods"], ["patch", "delete"]),
        ]
    return rules


def snapshot_rules(review: dict[str, Any], *, read_only: bool) -> list[Rule]:
    """Copy a SelfSubjectRulesReview into Role rules, optionally reads only.

    Structured rules rather than the ``auth can-i --list`` table, whose columns
    cannot round-trip resourceNames or API groups. The API server still applies
    its RBAC escalation checks to the Role this produces.
    """
    status = review.get("status") or {}
    rules = status.get("resourceRules")
    if (
        status.get("incomplete") is not False
        or status.get("evaluationError")
        or not isinstance(rules, list)
    ):
        raise ValueError("incomplete or invalid rules review")
    copied: list[Rule] = []
    for rule in rules:
        verbs = list(rule.get("verbs") or [])
        if read_only:
            verbs = (
                list(READ_VERBS)
                if "*" in verbs
                else [verb for verb in verbs if verb in READ_VERBS]
            )
        if not verbs:
            continue
        entry: Rule = {
            "apiGroups": rule.get("apiGroups") or [],
            "resources": rule.get("resources") or [],
            "verbs": verbs,
        }
        if rule.get("resourceNames"):
            entry["resourceNames"] = rule["resourceNames"]
        copied.append(entry)
    return copied


def manifest(account: str, namespace: str, rules: list[Rule]) -> dict[str, Any]:
    """Role and RoleBinding, never cluster-scoped: that is the confinement."""

    def meta() -> dict[str, Any]:
        return {"name": account, "namespace": namespace, "labels": MANAGED_BY}

    return {
        "apiVersion": "v1",
        "kind": "List",
        "items": [
            {"apiVersion": "v1", "kind": "ServiceAccount", "metadata": meta()},
            {
                "apiVersion": "rbac.authorization.k8s.io/v1",
                "kind": "Role",
                "metadata": meta(),
                "rules": rules,
            },
            {
                "apiVersion": "rbac.authorization.k8s.io/v1",
                "kind": "RoleBinding",
                "metadata": meta(),
                "roleRef": {
                    "apiGroup": "rbac.authorization.k8s.io",
                    "kind": "Role",
                    "name": account,
                },
                "subjects": [
                    {
                        "kind": "ServiceAccount",
                        "name": account,
                        "namespace": namespace,
                    }
                ],
            },
        ],
    }
