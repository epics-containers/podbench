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

    def limit_of(container: Any) -> Fraction | None:
        limits = as_dict(as_dict(as_dict(container).get("resources")).get("limits"))
        value = limits.get(STORAGE)
        return None if value is None else quantity(str(value))

    # Mirror the kubelet's PodLimits: app containers plus restartable (sidecar)
    # init containers run together; each one-shot init container runs with only
    # the sidecars started before it, and the pod limit is the larger of those.
    any_set = False
    sidecars = Fraction(0)
    init_peak = Fraction(0)
    for container in spec.get("initContainers", []):
        value = limit_of(container)
        any_set = any_set or value is not None
        if as_dict(container).get("restartPolicy") == "Always":
            sidecars += value or 0
        else:
            init_peak = max(init_peak, sidecars + (value or 0))
    running = Fraction(0)
    for container in spec.get("containers", []):
        value = limit_of(container)
        any_set = any_set or value is not None
        running += value or 0
    pod_limit = max(running + sidecars, init_peak) if any_set else None
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
    return pod_limit, reserved, unbounded


def warn_if_seat_may_not_fit(pod: dict[str, Any], target: str) -> None:
    """Print a warning when the seat could push the pod past its storage limit."""
    limit, reserved, unbounded = storage_budget(pod)
    if limit is None or (not unbounded and limit - reserved >= SEAT_STORAGE):
        return
    if unbounded:
        volumes = " and it has emptyDir volumes without a sizeLimit"
    elif reserved:
        volumes = f" and it has emptyDir sizeLimits of {_format(reserved, STORAGE)}"
    else:
        volumes = ""
    console.print(
        f"warning: this pod's {STORAGE} limit is {_format(limit, STORAGE)}"
        f"{volumes}; a new VS Code seat needs about "
        f"{_format(SEAT_STORAGE, STORAGE)} more. If the kubelet evicts the pod, "
        f"raise {target}'s limits.{STORAGE} in the service values "
        "(it cannot be resized in place).",
        style="yellow",
    )


def explain_lost_pod(kube: Kubectl, name: str, uid: str) -> str | None:
    """Say why the pod a seat was added to has gone, or None if it has not."""
    try:
        live = kube.get_pod(name)
    except KubectlError as error:
        if not _not_found(error):
            return None  # e.g. expired credentials: let the original error show.
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
    if not live:
        return f"pod {name} no longer exists (it was uid {uid})"
    return f"pod {name} was replaced (it is no longer uid {uid})"


def _not_found(error: KubectlError) -> bool:
    detail = error.result.stderr if error.result else str(error)
    return "NotFound" in detail or "not found" in detail
