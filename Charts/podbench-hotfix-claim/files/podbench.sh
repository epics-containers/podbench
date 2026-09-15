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
  # Helpers share these locals through Bash's dynamic function scope.
  local original_umask supervisor_pid control hold pidfile
  local child= owner= request= state=normal
  local -A descendants=()
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
  track() {
    local proc stat rest pid parent birth changed
    local -a fields
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
    local -a fields
    for pid in "${!descendants[@]}"; do
      IFS= read -r stat 2>/dev/null < "/proc/$pid/stat" || continue
      rest=${stat##*) }; read -r -a fields <<< "$rest"
      [ "${fields[19]}" != "${descendants[$pid]}" ] ||
        kill -"$1" "$pid" 2>/dev/null || true
    done
  }
  set_state() {
    state=$1
    holds
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
    local until_time
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
  # Both normal startup and debugger Launch inherit the application's umask.
  spawn() {
    (
      umask "$original_umask"
      exec setsid "$@"
    ) &
    child=$!
    printf '%s\n' "$child" > "$pidfile"
  }
  normal() {
    spawn env -i "${_podbench_environment[@]}" bash -c \
      'source "$2"; podbench_launch "$1"' podbench-child "$startup" "$runtime"
  }
  reply() {
    printf '%s\n' "$1" > "$request/response.new"
    mv "$request/response.new" "$request/response"
  }
  check_child() {
    local now beat rc=0
    if [ "$state" = debugger ]; then
      track
      now=$(date +%s)
      beat=$(cat "$owner/heartbeat" 2>/dev/null || echo 0)
      if ! alive || [ -e "$owner/cancel" ] || [ "$((now - beat))" -gt 10 ]; then
        if alive; then rc=130; else wait "$child" || rc=$?; fi
        terminate
        printf '%s\n' "$rc" > "$owner/exit.new"
        mv "$owner/exit.new" "$owner/exit"
        owner=
        set_state stopped
      fi
    elif [ "$state" = normal ] && ! alive; then
      wait "$child" || rc=$?
      terminate
      exit "$rc"
    fi
  }
  start() {
    local deadline expires
    if [ "$state" = normal ]; then reply ok; return; fi
    set_state starting
    normal
    sleep 0.2
    deadline=$(cat "$request/deadline" 2>/dev/null || echo 120)
    case "$deadline" in *[!0-9]*|'') deadline=120;; esac
    [ "$deadline" -le 3600 ] || deadline=3600
    expires=$((SECONDS + deadline))
    while alive && [ "$SECONDS" -lt "$expires" ]; do
      if [ -e "$request/cancel" ]; then
        terminate; set_state stopped
        reply 'error: request cancelled'; return
      fi
      if timeout --kill-after=1 5 bash -c "$health" >/dev/null 2>&1 && alive; then
        set_state normal
        reply ok; return
      fi
      sleep 0.2
    done
    terminate; set_state failed
    reply 'error: startup/health check failed; application remains stopped and held'
  }
  handle_request() {
    local action token input output error
    if [ -e "$request/cancel" ]; then
      reply 'error: request cancelled'; return
    fi
    action=$(cat "$request/action")
    case "$action" in
      stop|restart|launch|hold)
        if ! "$safe"; then
          reply 'error: liveness needs hold-aware exec wiring; update and roll out'
          return
        fi ;;
    esac
    case "$action" in
      stop|start|restart|launch)
        if [ "$state" = debugger ]; then
          reply 'error: a Launch session owns the application; stop debugging first'
          return
        fi ;;
    esac
    case "$action" in
      stop|restart)
        set_state stopped; terminate
        if [ "$action" = stop ]; then reply ok; else start; fi ;;
      launch)
        if [ "$state" = normal ]; then
          reply 'error: application is running; run podbench stop first'; return
        fi
        if [ ! -f "$request/run" ]; then
          reply 'error: missing launch payload'; return
        fi
        set_state debugger
        owner=$request
        exec {input}<> "$request/in" {output}> "$request/out" {error}> "$request/err"
        spawn bash "$request/run" <&$input >&$output 2>&$error
        exec {input}<&- {output}>&- {error}>&-
        reply ok ;;
      hold)
        touch "$control/holds/${request##*/}"; holds; reply ok ;;
      release)
        token=$(cat "$request/token")
        case "$token" in *[!a-zA-Z0-9_-]*|'')
          reply 'error: invalid hold owner'; return;;
        esac
        rm -f "$control/holds/$token"; holds; reply ok ;;
      start) start ;;
      *) reply 'error: unknown lifecycle action' ;;
    esac
  }
  trap 'terminate; exit 0' TERM INT
  normal
  set_state normal
  while :; do
    check_child
    for request in "$control"/requests/*; do
      [ -f "$request/ready" ] && [ ! -f "$request/response" ] || continue
      handle_request
    done
    sleep 0.1
  done
}
