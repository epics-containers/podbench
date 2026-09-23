"""Warn when a VS Code seat may not fit a pod's ephemeral storage, and say so after.

A seat's writable layer (the VS Code server, extensions and caches) is charged
to the pod's ephemeral-storage total, alongside disk-backed emptyDir volumes.
When that total passes the sum of the containers' limits the kubelet evicts
the whole pod, and ephemeral-storage limits cannot be resized in place.
"""

from __future__ import annotations

import json
from fractions import Fraction
from typing import Any

from .cli import console
from .ide_resources import _format, quantity
from .kubectl import Kubectl, KubectlError
from .model import as_dict

STORAGE = "ephemeral-storage"
# The VS Code server alone unpacked to about 1.2 GiB in measurements; allow
# for extensions and caches on top.
SEAT_STORAGE = quantity("1536Mi")


def storage_budget(pod: dict[str, Any]) -> tuple[Fraction | None, Fraction, bool]:
    """Return (pod limit, emptyDir sizeLimits, whether any emptyDir is unbounded).

    The pod limit is None when no container sets an ephemeral-storage limit,
    in which case the kubelet does not evict the pod for its total usage.
    """
    spec = as_dict(pod.get("spec"))
    # Only restartable (sidecar) init containers run alongside the others.
    running = [*spec.get("containers", [])] + [
        c
        for c in spec.get("initContainers", [])
        if as_dict(c).get("restartPolicy") == "Always"
    ]
    limits = [
        as_dict(as_dict(as_dict(c).get("resources")).get("limits")).get(STORAGE)
        for c in running
    ]
    set_limits = [quantity(str(value)) for value in limits if value is not None]
    reserved, unbounded = Fraction(0), False
    for volume in spec.get("volumes", []):
        empty_dir = as_dict(volume).get("emptyDir")
        if empty_dir is None or as_dict(empty_dir).get("medium") == "Memory":
            continue  # Memory-backed emptyDir is charged to memory instead.
        size = as_dict(empty_dir).get("sizeLimit")
        if size is None:
            unbounded = True
        else:
            reserved += quantity(str(size))
    return (sum(set_limits, Fraction(0)) if set_limits else None), reserved, unbounded


def warn_if_seat_may_not_fit(pod: dict[str, Any], target: str) -> None:
    """Print a warning when the seat could push the pod past its storage limit."""
    limit, reserved, unbounded = storage_budget(pod)
    if limit is None or (not unbounded and limit - reserved >= SEAT_STORAGE):
        return
    volumes = (
        "emptyDir volumes without a sizeLimit"
        if unbounded
        else f"emptyDir sizeLimits of {_format(reserved, STORAGE)}"
    )
    console.print(
        f"warning: this pod's {STORAGE} limit is {_format(limit, STORAGE)} and "
        f"it has {volumes}; the VS Code seat needs about "
        f"{_format(SEAT_STORAGE, STORAGE)} more. If the kubelet evicts the pod, "
        f"raise {target}'s limits.{STORAGE} in the service values "
        "(it cannot be resized in place).",
        style="yellow",
    )


def explain_lost_pod(kube: Kubectl, name: str, uid: str) -> str | None:
    """Say why the pod a seat was added to has gone, or None if it has not."""
    try:
        live = kube.get_pod(name)
    except KubectlError:
        live = {}
    status = as_dict(live.get("status"))
    if as_dict(live.get("metadata")).get("uid") == uid:
        if status.get("reason") != "Evicted":
            return None
        return f"pod {name} was evicted: {status.get('message', '')}".rstrip(": ")
    result = kube.run(
        "get",
        "events",
        "--field-selector",
        f"involvedObject.uid={uid},reason=Evicted",
        "-o",
        "json",
        check=False,
    )
    try:
        events = json.loads(result.stdout).get("items", []) if result.stdout else []
    except (ValueError, AttributeError):
        events = []
    messages = [as_dict(e).get("message", "") for e in events if isinstance(e, dict)]
    if messages:
        return f"pod {name} was evicted: {messages[-1]}"
    return f"pod {name} was replaced (it is no longer uid {uid})"
