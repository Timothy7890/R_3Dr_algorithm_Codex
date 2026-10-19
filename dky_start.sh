#!/usr/bin/env bash
# Usage: ./dky_start.sh <NODE_ID> [start|stop|restart|status]
#   e.g. ./dky_start.sh Server-1        (on clawd_dky_1)
#        ./dky_start.sh Server-4        (on clawd_dky_4)
#        ./dky_start.sh restart         (NODE_ID of the node running on this machine)
# The code directory may be shared between machines, so NODE_ID is passed
# here instead of being read from .env, and log/pid files are per node.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PYTHON="${COMPUTE_PYTHON:-/root/miniconda3/envs/plantsegnerf_pp/bin/python}"
HOST="${COMPUTE_HOST:-127.0.0.1}"
PORT="${COMPUTE_PORT:-14000}"

cd "$ROOT"
mkdir -p logs run

usage() {
    sed -n '2,5p' "$0" | sed 's/^# \{0,1\}//' >&2
    exit 2
}

health() {
    "$PYTHON" - "$HOST" "$PORT" <<'EOF'
import json, sys, urllib.request
opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
with opener.open(f"http://{sys.argv[1]}:{sys.argv[2]}/health", timeout=3) as r:
    print(json.dumps(json.load(r), ensure_ascii=False))
EOF
}

# NODE_ID of the node running on this machine: from a live PID file, else from /health.
detect_node_id() {
    local found=() file pid host
    for file in run/compute-*.pid; do
        [[ -f "$file" ]] || continue
        read -r pid host < "$file" || continue
        [[ "$host" == "$(hostname)" ]] && kill -0 "$pid" 2>/dev/null || continue
        file="${file#run/compute-}"
        found+=("${file%.pid}")
    done
    if (( ${#found[@]} == 1 )); then
        echo "${found[0]}"
    elif (( ${#found[@]} > 1 )); then
        echo "本机运行着多个节点 (${found[*]})，请指定 NODE_ID" >&2
        return 1
    elif [[ -x "$PYTHON" ]]; then
        health 2>/dev/null | "$PYTHON" -c 'import json, sys; print(json.load(sys.stdin)["node_id"])' 2>/dev/null
    else
        return 1
    fi
}

case "${1:-}" in
    start|stop|restart|status)
        ACTION="$1"
        if ! NODE_ID="$(detect_node_id)" || [[ -z "$NODE_ID" ]]; then
            echo "没有找到本机正在运行的节点，请指定 NODE_ID，例如: $0 Server-1 $ACTION" >&2
            usage
        fi
        echo "未指定 NODE_ID，使用本机正在运行的节点: $NODE_ID"
        ;;
    ""|-*) usage ;;
    *) NODE_ID="$1"; ACTION="${2:-start}" ;;
esac

LOG="$ROOT/logs/compute-$NODE_ID.log"
PIDFILE="$ROOT/run/compute-$NODE_ID.pid"

running_pid() {
    [[ -f "$PIDFILE" ]] || return 1
    local pid host
    read -r pid host < "$PIDFILE" || return 1
    if [[ "$host" != "$(hostname)" ]]; then
        echo "$NODE_ID 的 PID 文件属于另一台机器 ($host)，请在那台机器上操作" >&2
        exit 1
    fi
    kill -0 "$pid" 2>/dev/null && echo "$pid"
}

# PID listening on PORT, only when that service reports this NODE_ID (the PID file may have been lost).
port_pid() {
    local reported
    reported="$(health 2>/dev/null | "$PYTHON" -c 'import json, sys; print(json.load(sys.stdin)["node_id"])' 2>/dev/null)" || return 1
    [[ "$reported" == "$NODE_ID" ]] && command -v ss >/dev/null || return 1
    ss -Hltnp "sport = :$PORT" 2>/dev/null | grep -o 'pid=[0-9]*' | head -n 1 | cut -d= -f2
}

start() {
    local pid
    if pid="$(running_pid)"; then
        echo "$NODE_ID 已在运行 (pid $pid)"
        return 0
    fi
    [[ -x "$PYTHON" ]] || { echo "找不到 Python: $PYTHON" >&2; exit 1; }
    local occupant
    if occupant="$(health 2>/dev/null)"; then
        echo "$HOST:$PORT 已被其他进程占用: $occupant" >&2
        echo "如果这就是要重启的节点，请用它的 NODE_ID 执行: $0 <NODE_ID> restart" >&2
        exit 1
    fi

    echo "==== $(date '+%F %T') start $NODE_ID on $(hostname) ====" >> "$LOG"
    COMPUTE_NODE_ID="$NODE_ID" setsid nohup "$PYTHON" -m uvicorn compute_node.main:app \
        --host "$HOST" --port "$PORT" --no-access-log >> "$LOG" 2>&1 < /dev/null &
    echo "$! $(hostname)" > "$PIDFILE"

    for _ in $(seq 1 30); do
        sleep 1
        if ! running_pid >/dev/null; then
            echo "启动失败，日志最后 20 行:" >&2
            tail -n 20 "$LOG" >&2
            rm -f "$PIDFILE"
            exit 1
        fi
        if health 2>/dev/null; then
            echo "$NODE_ID 已启动 (pid $(running_pid))，日志: $LOG"
            return 0
        fi
    done
    echo "30 秒内 /health 未响应，请查看日志: $LOG" >&2
    exit 1
}

stop() {
    local pid
    if ! pid="$(running_pid)"; then
        if pid="$(port_pid)" && [[ -n "$pid" ]]; then
            echo "$NODE_ID 的 PID 文件缺失，按端口 $PORT 找到进程 (pid $pid)"
        else
            echo "$NODE_ID 未在运行"
            rm -f "$PIDFILE"
            return 0
        fi
    fi
    kill -TERM -- "-$pid" 2>/dev/null || kill -TERM "$pid"
    for _ in $(seq 1 15); do
        kill -0 "$pid" 2>/dev/null || break
        sleep 1
    done
    kill -0 "$pid" 2>/dev/null && kill -KILL -- "-$pid" 2>/dev/null || true
    rm -f "$PIDFILE"
    echo "$NODE_ID 已停止"
}

status() {
    local pid
    if pid="$(running_pid)"; then
        echo "$NODE_ID 运行中 (pid $pid)"
        health 2>/dev/null || echo "/health 无响应"
    else
        echo "$NODE_ID 未在运行"
    fi
}

case "$ACTION" in
    start) start ;;
    stop) stop ;;
    restart) stop; start ;;
    status) status ;;
    *) echo "未知操作: $ACTION (start|stop|restart|status)" >&2; exit 2 ;;
esac
