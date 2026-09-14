#!/bin/bash
# Run the podbench hotfix lifecycle against one pod and print evidence.
#
# Steps: baseline, stop, start, edit through the claim, restart, verify.
# Each step records the supervisor state, the hold file, the supervised
# child PID and the container restart count, so the report shows that the
# application changed without the container restarting.
#
# Usage:
#   hotfix-cycle.sh POD -n NAMESPACE [--container NAME] \
#       --edit 'SHELL COMMAND RUN IN THE CONTAINER' --expect REGEX
#
# --edit inserts a visible change into the claim at /podbench/app.
# --expect is grepped in the container log after the restart.
# Requires kubectl and podbench on PATH (or set PODBENCH="uv run podbench").
set -euo pipefail

pod=${1:?POD required}; shift
ns="" container="" edit="" expect=""
while [ $# -gt 0 ]; do
    case "$1" in
        -n|--namespace) ns=$2; shift 2 ;;
        --container) container=$2; shift 2 ;;
        --edit) edit=$2; shift 2 ;;
        --expect) expect=$2; shift 2 ;;
        *) echo "hotfix-cycle: unknown argument $1" >&2; exit 2 ;;
    esac
done
[ -n "$ns" ] || { echo "hotfix-cycle: -n NAMESPACE required" >&2; exit 2; }
[ -n "$edit" ] && [ -n "$expect" ] || { echo "hotfix-cycle: --edit and --expect required" >&2; exit 2; }
PODBENCH=${PODBENCH:-podbench}
copt=(); [ -z "$container" ] || copt=(-c "$container")
pbopt=(-n "$ns"); [ -z "$container" ] || pbopt+=(--container "$container")

fail=0
say() { printf '%-9s %s\n' "$1" "$2"; }
ok() { say "PASS" "$1"; }
bad() { say "FAIL" "$1"; fail=1; }
inpod() { kubectl exec "$pod" -n "$ns" "${copt[@]}" -- sh -c "$1" 2>/dev/null; }
snapshot() {
    # state child hold restarts
    local state child hold restarts
    state=$(inpod 'cat /tmp/podbench-control/state 2>/dev/null || echo legacy')
    child=$(inpod 'cat /tmp/podbench-child.pid 2>/dev/null || echo -')
    hold=$(inpod '[ -e /tmp/podbench-hold ] && echo held || echo free')
    restarts=$(kubectl get pod "$pod" -n "$ns" -o jsonpath="{.status.containerStatuses[?(@.name==\"${container:-$(kubectl get pod "$pod" -n "$ns" -o jsonpath='{.spec.containers[0].name}')}\")].restartCount}")
    echo "$state $child $hold $restarts"
}
report() { say "$1" "state=$2 child=$3 hold=$4 restarts=$5"; }

echo "== $pod ($ns${container:+, container $container})"
read -r s0 c0 h0 r0 <<< "$(snapshot)"; report baseline "$s0" "$c0" "$h0" "$r0"
if [ "$s0" = legacy ]; then
    bad "no lifecycle control channel: legacy wiring. Run 'podbench hotfix enable' on the chart, commit, roll out, then rerun."
    exit 1
fi

$PODBENCH stop "$pod" "${pbopt[@]}" >/dev/null
read -r s1 c1 h1 r1 <<< "$(snapshot)"; report stop "$s1" "$c1" "$h1" "$r1"
[ "$s1" = stopped ] && [ "$h1" = held ] && ok "stop leaves the app stopped and held" || bad "stop did not reach stopped+held"

$PODBENCH start "$pod" "${pbopt[@]}" >/dev/null
read -r s2 c2 h2 r2 <<< "$(snapshot)"; report start "$s2" "$c2" "$h2" "$r2"
[ "$s2" = normal ] && [ "$h2" = free ] && [ "$c2" != "-" ] && ok "start returns to normal with a child" || bad "start did not return to normal"

inpod "$edit" >/dev/null && ok "edit applied in the claim" || bad "edit command failed"
before=$(kubectl logs "$pod" -n "$ns" "${copt[@]}" 2>/dev/null | grep -cE "$expect" || true)

$PODBENCH restart "$pod" "${pbopt[@]}" >/dev/null
sleep 5
read -r s3 c3 h3 r3 <<< "$(snapshot)"; report restart "$s3" "$c3" "$h3" "$r3"
[ "$s3" = normal ] && [ "$c3" != "$c2" ] && ok "restart replaced the child ($c2 -> $c3)" || bad "restart did not replace the child"
[ "$r3" = "$r0" ] && ok "container restart count unchanged ($r0)" || bad "container restarted ($r0 -> $r3)"

for _ in 1 2 3 4 5 6; do
    after=$(kubectl logs "$pod" -n "$ns" "${copt[@]}" --since=3m 2>/dev/null | grep -cE "$expect" || true)
    [ "$after" -gt 0 ] && break; sleep 5
done
[ "${after:-0}" -gt 0 ] && ok "edit visible in the log ($after match(es) for /$expect/)" || bad "edit not visible in the log"

[ "$fail" = 0 ] && say RESULT "all checks passed" || say RESULT "some checks failed"
exit "$fail"
