#!/usr/bin/env bash
# start.sh — 컵 스태킹 로봇 시스템 통합 실행 스크립트
#
# 사용법:
#   ./start.sh              # rosbridge + 카메라 + bringup-agent + Docker 서버
#
# bringup은 웹 대시보드(https://yarr.simplyimg.com)에서 버튼으로 제어합니다.

set -e

ROS_SETUP="/opt/ros/humble/setup.bash"
SCRIPT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
SESSION="cup-stack"

# ── 사전 확인 ──────────────────────────────────────────────
if [[ ! -f "$ROS_SETUP" ]]; then
    echo "[ERROR] ROS 2 Humble not found at $ROS_SETUP" >&2
    exit 1
fi

if ! command -v tmux &>/dev/null; then
    echo "[ERROR] tmux이 설치되지 않았습니다. sudo apt install tmux" >&2
    exit 1
fi

if ! command -v docker &>/dev/null; then
    echo "[ERROR] Docker가 설치되지 않았습니다." >&2
    exit 1
fi

# ── 기존 세션 정리 ────────────────────────────────────────
if tmux has-session -t "$SESSION" 2>/dev/null; then
    echo "[INFO] 기존 tmux 세션 '$SESSION' 종료 중..."
    tmux kill-session -t "$SESSION"
fi

echo "[INFO] tmux 세션 '$SESSION' 시작..."
tmux new-session -d -s "$SESSION" -x 220 -y 50

# ── 창 0: rosbridge ───────────────────────────────────────
tmux rename-window -t "$SESSION:0" "rosbridge"
tmux send-keys -t "$SESSION:rosbridge" \
    "bash $SCRIPT_DIR/rosbridge.sh" Enter

# ── 창 1: RealSense 카메라 ───────────────────────────────
tmux new-window -t "$SESSION" -n "camera"
tmux send-keys -t "$SESSION:camera" \
    "source $ROS_SETUP && ros2 launch realsense2_camera rs_launch.py align_depth.enable:=true" Enter

# ── 창 2: bringup 에이전트 (포트 8099) ────────────────────
tmux new-window -t "$SESSION" -n "bringup-agent"
tmux send-keys -t "$SESSION:bringup-agent" \
    "python3 $SCRIPT_DIR/bringup_agent.py" Enter

# ── 창 3: Docker 서버 (nginx + FastAPI + cloudflared) ────
tmux new-window -t "$SESSION" -n "server"
tmux send-keys -t "$SESSION:server" \
    "cd $SCRIPT_DIR && docker compose up" Enter

# ── 포커스 ──────────────────────────────────────────────
tmux select-window -t "$SESSION:rosbridge"

echo ""
echo "======================================================"
echo " 컵 스태킹 로봇 시스템 시작 완료"
echo "======================================================"
echo " 세션 연결:   tmux attach -t $SESSION"
echo " 창 전환:     Ctrl+b → 숫자"
echo "   0 = rosbridge   1 = camera"
echo "   2 = bringup-agent (port 8099)"
echo "   3 = server (Docker)"
echo " 세션 종료:   tmux kill-session -t $SESSION"
echo ""
echo " 대시보드:    https://yarr.simplyimg.com"
echo " API:         https://yarr-api.simplyimg.com/api/robot/status"
echo " Bringup 제어: 대시보드 헤더의 Bringup 버튼 사용"
echo "======================================================"

tmux attach -t "$SESSION"
