#!/usr/bin/env bash
# stop.sh — 컵 스태킹 로봇 시스템 전체 종료 스크립트

SESSION="cup-stack"
SCRIPT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)

echo "[INFO] 컵 스태킹 시스템 종료 중..."

# ── 1. bringup (dsr_bringup2) ────────────────────────────
if pgrep -f "dsr_bringup2" &>/dev/null; then
    echo "[INFO] bringup 종료..."
    pkill -SIGINT -f "dsr_bringup2" 2>/dev/null || true
    sleep 2
    pkill -SIGKILL -f "dsr_bringup2" 2>/dev/null || true
fi

# ── 1-2. cup_stack tasks (move_cartesian, etc.) ─────────
if pgrep -f "cup_stack" &>/dev/null; then
    echo "[INFO] cup_stack 관련 프로세스 종료..."
    pkill -SIGINT -f "cup_stack" 2>/dev/null || true
    sleep 1
    pkill -SIGKILL -f "cup_stack" 2>/dev/null || true
fi

# ── 2. RealSense 카메라 ──────────────────────────────────
if pgrep -f "realsense2_camera" &>/dev/null; then
    echo "[INFO] RealSense 카메라 종료..."
    pkill -SIGINT -f "realsense2_camera" 2>/dev/null || true
    sleep 1
    pkill -SIGKILL -f "realsense2_camera" 2>/dev/null || true
fi

# ── 3. rosbridge ─────────────────────────────────────────
if pgrep -f "rosbridge_websocket" &>/dev/null; then
    echo "[INFO] rosbridge 종료..."
    pkill -SIGINT -f "rosbridge_websocket" 2>/dev/null || true
    sleep 1
    pkill -SIGKILL -f "rosbridge_websocket" 2>/dev/null || true
fi

# ── 3-2. bringup-agent (port 8099) ───────────────────────
if pgrep -f "bringup_agent.py" &>/dev/null; then
    echo "[INFO] bringup-agent 종료..."
    pkill -f "bringup_agent.py" 2>/dev/null || true
fi

# ── 4. Docker Compose ────────────────────────────────────
if docker compose -f "$SCRIPT_DIR/docker-compose.yml" ps -q 2>/dev/null | grep -q .; then
    echo "[INFO] Docker 서비스 종료..."
    docker compose -f "$SCRIPT_DIR/docker-compose.yml" down
fi

# ── 5. tmux 세션 ─────────────────────────────────────────
if tmux has-session -t "$SESSION" 2>/dev/null; then
    echo "[INFO] tmux 세션 '$SESSION' 종료..."
    tmux kill-session -t "$SESSION"
fi

echo ""
echo "======================================================"
echo " 종료 완료"
echo "======================================================"
