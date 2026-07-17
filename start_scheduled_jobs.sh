#!/bin/bash

# 若被 sh 调用，自动切换到 bash 重新执行
if [ -z "${BASH_VERSION:-}" ]; then
    echo "[WARN] 检测到当前非 bash，自动使用 bash 重新执行脚本..."
    exec bash "$0" "$@"
fi

# ======================================
# basic_rq 定时任务服务启动脚本 (Ubuntu)
# 功能：
#   1. 启动前检查目标端口；若已被占用则终止占用进程后再启动
#   2. 启动 main.py（交易日按计划执行日更任务）
#   3. 支持自定义 HTTP 端口（Mongo 连接见 mongo_connect.py / .env 中 MONGO_TRADE_ALIAS）
# ======================================

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR" || exit 1

# --- 配置参数 ---
PORT=${1:-7331}
LOG_FILE="${SCRIPT_DIR}/scheduled_jobs.log"
PY_CMD="python3"
PIP_CMD="pip3"
REQ_FILE="requirements-basic_rq_daily.txt"

export RQBASE_SCHEDULE_PORT="$PORT"
export TZ=Asia/Shanghai

# --- 激活虚拟环境 ---
activate_venv() {
    if [ -f "venv/bin/activate" ]; then
        # shellcheck disable=SC1091
        . "venv/bin/activate"
        if command -v python >/dev/null 2>&1 && command -v pip >/dev/null 2>&1; then
            PY_CMD="python"
            PIP_CMD="pip"
            echo "[INFO] 已激活虚拟环境: venv/bin/activate"
            return 0
        fi
        echo "[WARN] 虚拟环境激活后未找到 python/pip，继续使用系统环境。"
        return 0
    fi
    echo "[WARN] 未找到 venv/bin/activate，继续使用系统 Python: $PY_CMD"
    return 0
}

# --- 自动安装 Python 依赖 ---
auto_install_python_deps() {
    local install_target="$1"
    local mirror_url="${PIP_INDEX_URL:-https://pypi.tuna.tsinghua.edu.cn/simple}"
    local official_url="https://pypi.org/simple"

    if ! command -v "$PIP_CMD" >/dev/null 2>&1; then
        echo "[ERROR] 未找到 pip 命令: $PIP_CMD"
        exit 1
    fi

    echo "[INFO] 尝试通过镜像源安装依赖: $install_target"
    if "$PIP_CMD" install -i "$mirror_url" $install_target; then
        echo "[INFO] 依赖安装成功（镜像源）。"
        return 0
    fi

    echo "[WARN] 镜像源安装失败，自动回退官方源..."
    if "$PIP_CMD" install -i "$official_url" $install_target; then
        echo "[INFO] 依赖安装成功（官方源）。"
        return 0
    fi

    echo "[ERROR] 自动安装依赖失败: $install_target"
    exit 1
}

# --- Linux 运行环境预检查 ---
precheck_linux_env() {
    echo "[INFO] 开始进行 Linux 运行环境检查..."

    if ! command -v "$PY_CMD" >/dev/null 2>&1; then
        echo "[ERROR] 未找到 Python 命令: $PY_CMD"
        exit 1
    fi

    local missing_mods=()
    local check_mods=("dotenv" "pymongo" "pandas" "numpy" "apscheduler" "loguru" "rqdatac")
    local m=""
    for m in "${check_mods[@]}"; do
        "$PY_CMD" -c "import ${m}" >/dev/null 2>&1 || missing_mods+=("${m}")
    done
    if [ ${#missing_mods[@]} -gt 0 ]; then
        echo "[WARN] 缺少 Python 依赖模块: ${missing_mods[*]}"
        if [ -f "$REQ_FILE" ]; then
            auto_install_python_deps "-r ${REQ_FILE}"
        else
            echo "[ERROR] 未找到 ${REQ_FILE}"
            exit 1
        fi
    fi

    if [ ! -f ".env" ]; then
        echo "[WARN] 未找到 .env，请复制 .env.example 并配置 MONGO_TRADE_ALIAS、ALPHA_NOTIFY_*"
    fi

    if [ ! -f "main.py" ]; then
        echo "[ERROR] 未找到 main.py，请确认在 basic_rq_daily 根目录执行。"
        exit 1
    fi

    echo "[INFO] Linux 运行环境检查通过。"
}

# --- 若目标端口已被占用则终止占用进程 ---
# 说明：无 root 时 ss -lptp 常不显示 pid=，不能仅凭「无 pid」判断空闲。
port_is_listening() {
    if command -v ss >/dev/null 2>&1; then
        # 只看是否 LISTEN，不依赖 pid（无 sudo 时 pid 常为空）
        ss -ltn "( sport = :$PORT )" 2>/dev/null | grep -q LISTEN
        return $?
    fi
    if command -v "$PY_CMD" >/dev/null 2>&1; then
        "$PY_CMD" -c "
import socket
s = socket.socket()
s.settimeout(0.5)
try:
    s.connect(('127.0.0.1', int('$PORT')))
    raise SystemExit(0)
except Exception:
    raise SystemExit(1)
finally:
    s.close()
" 2>/dev/null
        return $?
    fi
    return 1
}

collect_port_pids() {
    local pids=""
    if command -v lsof >/dev/null 2>&1; then
        pids=$(lsof -tiTCP:"$PORT" -sTCP:LISTEN 2>/dev/null || true)
    fi
    if [ -z "$pids" ] && command -v ss >/dev/null 2>&1; then
        pids=$(ss -lptn "sport = :$PORT" 2>/dev/null | sed -n 's/.*pid=\([0-9]*\).*/\1/p' | sort -u | tr '\n' ' ')
    fi
    # 仍无 pid：按进程名兜底（本仓库服务）
    if [ -z "$pids" ]; then
        pids=$(pgrep -f "[p]ython([0-9.]*)? .*main\\.py.*--port[= ]*$PORT" 2>/dev/null || true)
        if [ -z "$pids" ]; then
            pids=$(pgrep -f "[p]ython([0-9.]*)? .*main\\.py" 2>/dev/null || true)
        fi
    fi
    echo "$pids" | tr '\n' ' ' | sed 's/[[:space:]]\+/ /g' | sed 's/^ //;s/ $//'
}

ensure_port_free() {
    echo "[INFO] 检查端口 $PORT 是否被占用..."

    if ! port_is_listening; then
        echo "[INFO] 端口 $PORT 空闲。"
        return 0
    fi

    local pids
    pids=$(collect_port_pids)
    if [ -n "$pids" ]; then
        echo "[WARN] 端口 $PORT 已被占用 (PID: $pids)，正在终止..."
        # shellcheck disable=SC2086
        kill -TERM $pids 2>/dev/null || true
        sleep 2
        if port_is_listening; then
            pids=$(collect_port_pids)
            if [ -n "$pids" ]; then
                echo "[WARN] 仍占用，强制 kill -KILL: $pids"
                # shellcheck disable=SC2086
                kill -KILL $pids 2>/dev/null || true
                sleep 1
            fi
        fi
    elif command -v fuser >/dev/null 2>&1; then
        echo "[WARN] 端口 $PORT 有监听但无法解析 PID，尝试 fuser 释放..."
        fuser -k -TERM "$PORT/tcp" 2>/dev/null || true
        sleep 2
        fuser -k -KILL "$PORT/tcp" 2>/dev/null || true
        sleep 1
    else
        echo "[ERROR] 端口 $PORT 已被占用，但当前用户看不到 PID（ss 无 pid=）。"
        echo "[ERROR] 请手动执行后重试："
        echo "        ss -ltnp | grep $PORT"
        echo "        pgrep -af 'main.py'"
        echo "        kill <PID>   # 必要时 kill -9 <PID>"
        exit 1
    fi

    if port_is_listening; then
        echo "[ERROR] 未能释放端口 $PORT，请手动 kill 占用进程后重试。"
        echo "        ss -ltnp | grep $PORT"
        echo "        pgrep -af 'main.py'"
        exit 1
    fi
    echo "[INFO] 已释放端口 $PORT"
}

# --- 启动定时任务服务 ---
start_server() {
    echo "[INFO] 启动 basic_rq 定时任务服务 (Port: $PORT)..."
    echo "[INFO] 计划任务见 scheduled_jobs/jobs/schedule_config.py"
    echo "[INFO] 健康检查: http://192.168.110.199:${PORT}/health"
    nohup "$PY_CMD" main.py --port "$PORT" >> "$LOG_FILE" 2>&1 &
    local pid=$!
    sleep 1
    if kill -0 "$pid" 2>/dev/null; then
        echo "[SUCCESS] 定时任务服务已启动 (PID: $pid)"
        echo "[INFO] 日志文件: $LOG_FILE"
        echo "[INFO] Mongo 连接见 mongo_connect.py（.env 可设 MONGO_TRADE_ALIAS）"
    else
        echo "[ERROR] 启动失败，请查看 $LOG_FILE"
        exit 1
    fi
}

main() {
    activate_venv
    precheck_linux_env
    ensure_port_free
    start_server
}

main
