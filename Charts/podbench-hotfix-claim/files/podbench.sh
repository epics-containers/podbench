#!/bin/bash
# Shared hotfix runtime. Sourcing this file only defines functions.

# Prefer a checkout's interpreter, retaining the original module and arguments.
podbench_python() {
  local python=$1
  shift
  if [ -x /podbench/app/.venv/bin/python ]; then
    python=/podbench/app/.venv/bin/python
  fi
  exec "$python" "$@"
}

# Prefer an editable shell script, preserving the image command as a fallback.
podbench_script() {
  local editable=$1
  shift
  if [ -f "$editable" ]; then
    exec bash "$editable"
  fi
  exec "$@"
}

# Apply checkout settings only in the child, after restoring its startup environment.
podbench_launch() {
  [ ! -x /podbench/app/.venv/bin/python ] ||
    export PATH="/podbench/app/.venv/bin:$PATH"
  [ ! -f /podbench/app/.podbench-ptrace.so ] ||
    export LD_PRELOAD="/podbench/app/.podbench-ptrace.so${LD_PRELOAD:+:$LD_PRELOAD}"
  eval "$1"
}

# Inherit the container's environment, working directory and execution identity.
# The command is shell text; health and hold safety come from the original probes.
podbench_supervise() {
  # Keep exported application names independent of the supervisor's variables.
  local -a _podbench_environment=()
  local _podbench_entry
  while IFS= read -r -d '' _podbench_entry; do
    _podbench_environment+=("$_podbench_entry")
  done < <(env -0)
  local startup=$1 health=$2 safe=$3 runtime=${BASH_SOURCE[0]}
  original_umask=$(umask)
  umask 077
  supervisor_pid=$BASHPID
  control=/tmp/podbench-control
  hold=/tmp/podbench-hold
  pidfile=/tmp/podbench-child.pid
  # /tmp may survive a container restart; never restore development state.
  rm -rf "$control"
  rm -f "$hold" "$pidfile"
  mkdir -m 700 "$control" "$control/requests" "$control/holds"
  printf '1\n' > "$control/version"
  printf '%s\n' "$supervisor_pid" > "$control/supervisor"
  child= owner= request=
  state=normal
  declare -A descendants
  track() {
    local proc stat rest pid parent birth changed
    changed=true
    while "$changed"; do
      changed=false
      for proc in /proc/[0-9]*/stat; do
        IFS= read -r stat 2>/dev/null < "$proc" || continue
        pid=${stat%% *}; rest=${stat##*) }
        [ /proc/"$pid"/ns/mnt -ef /proc/"$supervisor_pid"/ns/mnt ] || continue
        read -r -a fields <<< "$rest"
        parent=${fields[1]}; birth=${fields[19]}
        [ "$pid" != "$supervisor_pid" ] || continue
        if [ "$parent" = "$child" ] || [ "$parent" = "$supervisor_pid" ] ||
            [ -n "${descendants[$parent]:-}" ]; then
          if [ "${descendants[$pid]:-}" != "$birth" ]; then
            descendants[$pid]=$birth; changed=true
          fi
        fi
      done
    done
  }
  kill_tree() {
    local pid stat rest
    for pid in "${!descendants[@]}"; do
      IFS= read -r stat 2>/dev/null < "/proc/$pid/stat" || continue
      rest=${stat##*) }; read -r -a fields <<< "$rest"
      [ "${fields[19]}" != "${descendants[$pid]}" ] ||
        kill -"$1" "$pid" 2>/dev/null || true
    done
  }
  set_state() {
    state=$1
    printf '%s\n' "$state" > "$control/state.new"
    mv "$control/state.new" "$control/state"
  }
  holds() {
    if [ "$state" != normal ] || [ -n "$(ls -A "$control/holds")" ]; then
      touch "$hold"
    else
      rm -f "$hold"
    fi
  }
  alive() { [ -n "$child" ] && kill -0 "$child" 2>/dev/null; }
  terminate() {
    [ -n "$child" ] || return 0
    track
    kill_tree TERM
    kill -TERM -"$child" 2>/dev/null || kill -TERM "$child" 2>/dev/null || true
    until_time=$((SECONDS + 5))
    while kill -0 -"$child" 2>/dev/null && [ "$SECONDS" -lt "$until_time" ]; do
      sleep 0.1
    done
    kill_tree KILL
    descendants=()
    kill -KILL -"$child" 2>/dev/null || true
    wait "$child" 2>/dev/null || true
    child=
    rm -f "$pidfile"
  }
  normal() {
    (
      umask "$original_umask"
      exec setsid env -i "${_podbench_environment[@]}" bash -c \
        'source "$2"; podbench_launch "$1"' podbench-child "$startup" "$runtime"
    ) &
    child=$!
    printf '%s\n' "$child" > "$pidfile"
  }
  reply() {
    printf '%s\n' "$1" > "$request/response.new"
    mv "$request/response.new" "$request/response"
  }
  trap 'terminate; exit 0' TERM INT
  normal
  set_state normal
  while :; do
    if [ "$state" = debugger ]; then
      track
      now=$(date +%s)
      beat=$(cat "$owner/heartbeat" 2>/dev/null || echo 0)
      if ! alive || [ -e "$owner/cancel" ] || [ "$((now - beat))" -gt 10 ]; then
        rc=0
        if alive; then terminate; rc=130; else wait "$child" || rc=$?; terminate; fi
        printf '%s\n' "$rc" > "$owner/exit.new"
        mv "$owner/exit.new" "$owner/exit"
        owner=
        set_state stopped
        holds
      fi
    elif [ "$state" = normal ] && ! alive; then
      rc=0; wait "$child" || rc=$?
      terminate
      exit "$rc"
    fi
    for request in "$control"/requests/*; do
      [ -f "$request/ready" ] && [ ! -f "$request/response" ] || continue
      if [ -e "$request/cancel" ]; then
        reply 'error: request cancelled'; continue
      fi
      action=$(cat "$request/action")
      case "$action" in
        stop|restart|launch|hold)
          if ! "$safe"; then
            reply 'error: liveness needs hold-aware exec wiring; update and roll out'
            continue
          fi ;;
      esac
      case "$action" in
        stop|start|restart|launch)
          if [ "$state" = debugger ]; then
            reply 'error: a Launch session owns the application; stop debugging first'
            continue
          fi ;;
      esac
      case "$action" in
        stop|restart)
          set_state stopped; holds; terminate
          if [ "$action" = stop ]; then reply ok; continue; fi ;;
        launch)
          if [ "$state" = normal ]; then
            reply 'error: application is running; run podbench stop first'; continue
          fi
          if [ ! -f "$request/run" ]; then
            reply 'error: missing launch payload'; continue
          fi
          set_state debugger; holds
          owner=$request
          exec {input}<> "$request/in" {output}> "$request/out" {error}> "$request/err"
          setsid bash "$request/run" <&$input >&$output 2>&$error &
          child=$!
          exec {input}<&- {output}>&- {error}>&-
          printf '%s\n' "$child" > "$pidfile"
          reply ok; continue ;;
        hold)
          touch "$control/holds/$(basename "$request")"; holds; reply ok; continue ;;
        release)
          token=$(cat "$request/token")
          case "$token" in *[!a-zA-Z0-9_-]*|'')
            reply 'error: invalid hold owner'; continue;;
          esac
          rm -f "$control/holds/$token"; holds; reply ok; continue ;;
        start) ;;
        *) reply 'error: unknown lifecycle action'; continue ;;
      esac
      if [ "$state" = normal ]; then reply ok; continue; fi
      set_state starting; holds
      normal
      sleep 0.2
      deadline=$(cat "$request/deadline" 2>/dev/null || echo 120)
      case "$deadline" in *[!0-9]*|'') deadline=120;; esac
      [ "$deadline" -le 3600 ] || deadline=3600
      expires=$((SECONDS + deadline))
      while alive && [ "$SECONDS" -lt "$expires" ]; do
        if [ -e "$request/cancel" ]; then
          terminate; set_state stopped; holds
          reply 'error: request cancelled'; continue 2
        fi
        if timeout 5 bash -c "$health" >/dev/null 2>&1 && alive; then
          set_state normal; holds; break
        fi
        sleep 0.2
      done
      if [ "$state" != normal ]; then
        terminate; set_state failed; holds
        reply 'error: startup/health check failed; application remains stopped and held'
      else
        reply ok
      fi
    done
    sleep 0.1
  done
}
