"""Reserve editor headroom on a live pod, without changing its GitOps template."""

from __future__ import annotations

import json
import re
import time
from fractions import Fraction
from typing import Any

from .cli import console
from .kubectl import Kubectl, KubectlError
from .model import as_dict

BASELINE = "podbench.io/ide-resources"
# A modest guaranteed floor that fits a small beamline node and namespace quota,
# with a burst ceiling for language servers, debuggers and builds.
HEADROOM = {
    "requests": {"cpu": "500m", "memory": "1Gi"},
    "limits": {"cpu": "2", "memory": "4Gi"},
}


def quantity(value: str) -> Fraction:
    """Parse a Kubernetes resource quantity exactly, in cores or bytes."""
    match = re.fullmatch(
        r"([+-]?(?:\d+(?:\.\d*)?|\.\d+))([a-zA-Z]*|[eE][+-]?\d+)", str(value)
    )
    if not match:
        raise KubectlError(f"unsupported resource quantity: {value}")
    number, suffix = match.groups()
    powers = {
        "": 1,
        "n": Fraction(1, 10**9),
        "u": Fraction(1, 10**6),
        "m": Fraction(1, 1000),
    }
    powers.update({s: 1000**i for i, s in enumerate("kMGTPE", 1)})
    powers.update({s + "i": 1024**i for i, s in enumerate("KMGTPE", 1)})
    if suffix.startswith(("e", "E")) and len(suffix) > 1:
        return Fraction(number) * Fraction(10) ** int(suffix[1:])
    if suffix not in powers:
        raise KubectlError(f"unsupported resource suffix: {suffix}")
    return Fraction(number) * powers[suffix]


def _format(value: Fraction, resource: str) -> str:
    if resource == "cpu":
        milli = value * 1000
        return f"{-(-milli.numerator // milli.denominator)}m"
    count = -(-value.numerator // value.denominator)
    for unit, size in (("Gi", 1024**3), ("Mi", 1024**2), ("Ki", 1024)):
        if count % size == 0:
            return f"{count // size}{unit}"
    return str(count)


def ensure_headroom(kube: Kubectl, name: str, target: str, timeout: float) -> None:
    """Reserve editor resources on this pod and wait for the node to allocate them."""
    pod = kube.get_pod(name)
    meta, spec = as_dict(pod.get("metadata")), as_dict(pod.get("spec"))
    owners = meta.get("ownerReferences", [])
    if not any(
        o.get("controller")
        and o.get("kind") in ("ReplicaSet", "StatefulSet", "DaemonSet", "Job")
        for o in owners
    ):
        raise KubectlError(
            "IDE resize requires a controller-owned pod; "
            "a directly managed Pod may be reconciled back"
        )
    if spec.get("resources"):
        raise KubectlError(
            "pod-level resource budgets are not supported by IDE resize yet"
        )
    qos = as_dict(pod.get("status")).get("qosClass")
    if qos not in ("Guaranteed", "Burstable"):
        raise KubectlError(
            "IDE headroom cannot be reserved without changing this pod's QoS class"
        )
    container = next(c for c in spec["containers"] if c["name"] == target)
    current = as_dict(container.get("resources"))
    if current.get("claims") or any(
        p.get("restartPolicy") == "RestartContainer"
        for p in container.get("resizePolicy", [])
    ):
        raise KubectlError(
            "this container cannot be resized without a possible application restart"
        )
    annotations = as_dict(meta.get("annotations"))
    if "/Pod:" in str(annotations.get("argocd.argoproj.io/tracking-id", "")):
        raise KubectlError(
            "Argo CD directly tracks this Pod; resize its GitOps source instead"
        )
    try:
        baselines = json.loads(annotations.get(BASELINE, "{}"))
        if not isinstance(baselines, dict):
            raise ValueError("expected an object")
    except (ValueError, TypeError) as error:
        raise KubectlError(f"invalid {BASELINE} annotation") from error
    if target not in baselines:
        # Persist the original budget so reconnects do not add headroom repeatedly.
        baselines[target] = current
        kube.run(
            "patch",
            "pod",
            name,
            "--type=merge",
            "-p",
            json.dumps(
                {
                    "metadata": {
                        "resourceVersion": meta["resourceVersion"],
                        "annotations": {BASELINE: json.dumps(baselines)},
                    }
                }
            ),
        )
        pod = kube.get_pod(name)
    baseline = baselines[target]
    desired: dict[str, Any] = {key: dict(as_dict(current.get(key))) for key in HEADROOM}
    for resource in ("cpu", "memory"):
        for kind in HEADROOM:
            old = as_dict(baseline.get(kind)).get(resource)
            now = as_dict(current.get(kind)).get(resource)
            if kind == "limits" and now is None:
                continue  # An unlimited resource must remain unlimited.
            value = max(
                quantity(now or "0"),
                quantity(old or "0") + quantity(HEADROOM[kind][resource]),
            )
            desired[kind][resource] = _format(value, resource)
        if qos == "Guaranteed":
            # Equal requests and limits preserve the pod's existing QoS class.
            desired["requests"][resource] = desired["limits"][resource]
    ranges = json.loads(kube.run("get", "limitrange", "-o", "json").stdout)
    for item in ranges.get("items", []):
        for limit in as_dict(item.get("spec")).get("limits", []):
            if limit.get("type") != "Container":
                continue
            for resource, ratio in limit.get("maxLimitRequestRatio", {}).items():
                if resource in ("cpu", "memory") and resource in desired["limits"]:
                    request = max(
                        quantity(desired["requests"][resource]),
                        quantity(desired["limits"][resource]) / quantity(ratio),
                    )
                    desired["requests"][resource] = _format(request, resource)
    changed = any(
        quantity(str(value)) != quantity(str(as_dict(current.get(kind)).get(key, "0")))
        for kind, values in desired.items()
        for key, value in values.items()
    )
    if changed:
        summary = "; ".join(
            f"{kind} " + " ".join(f"{key}={value}" for key, value in values.items())
            for kind, values in desired.items()
        )
        console.print(f"Reserving VS Code headroom on {name}/{target}: {summary}")
        kube.run(
            "patch",
            "pod",
            name,
            "--subresource=resize",
            "--type=strategic",
            "-p",
            json.dumps(
                {
                    "metadata": {"resourceVersion": pod["metadata"]["resourceVersion"]},
                    "spec": {"containers": [{"name": target, "resources": desired}]},
                }
            ),
        )
    # An accepted spec patch does not mean the node has allocated the resources.
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        live = kube.get_pod(name)
        if live["metadata"]["uid"] != meta["uid"]:
            raise KubectlError(
                "pod was replaced while reserving IDE resources; rerun the command"
            )
        status = as_dict(live.get("status"))
        pending = [
            c
            for c in status.get("conditions", [])
            if c.get("type") in ("PodResizePending", "PodResizeInProgress")
            and c.get("status") == "True"
        ]
        if (
            any(c.get("reason") == "Infeasible" for c in pending)
            or status.get("resize") == "Infeasible"
        ):
            raise KubectlError(f"IDE resize is infeasible on this node: {pending}")
        actual = next(
            (
                c.get("resources", {})
                for c in status.get("containerStatuses", [])
                if c.get("name") == target
            ),
            {},
        )
        if (
            not pending
            and status.get("resize", "") in ("", "None")
            and all(
                quantity(str(as_dict(actual.get(kind)).get(key, "0")))
                >= quantity(str(value))
                for kind, values in desired.items()
                for key, value in values.items()
            )
        ):
            return
        time.sleep(1)
    raise KubectlError(
        "IDE resources are not allocated yet; inspect pod resize conditions "
        "and rerun (VS Code was not started)"
    )
