#!/usr/bin/env bash
# start.sh — 컵 스태킹 로봇 시스템 통합 실행 스크립트
#
# 사용법:
#   ./start.sh              # 기본 (rosbridge + 카메라 + Docker 서버)
#   ./start.sh sim          # 시뮬레이션 bringup 포함
#   ./start.sh real [IP]    # 실로봇 bringup 포함 (기본 IP: 192.168.1.100)

set -e

MODE=${1:-"real"}
ROBOT_IP=${2:-"192.168.1.100"}
ROS_SETUP="/opt/ros/humble/setup.bash"
SCRIPT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
SERVER_DIR="$SCRIPT_DIR"
CUP_STACK_DIR="$SCRIPT_DIR/../ros2-cup-stack/ros2/src/cup_stack"
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

# ── 창 1: rosbridge ───────────────────────────────────────
tmux rename-window -t "$SESSION:0" "rosbridge"
tmux send-keys -t "$SESSION:rosbridge" \
    "bash $SERVER_DIR/rosbridge.sh" Enter

# ── 창 2: RealSense 카메라 ───────────────────────────────
tmux new-window -t "$SESSION" -n "camera"
tmux send-keys -t "$SESSION:camera" \
    "source $ROS_SETUP && ros2 launch realsense2_camera rs_launch.py align_depth.enable:=true" Enter

# ── 창 3: Docker 서버 (nginx + FastAPI + cloudflared) ────
tmux new-window -t "$SESSION" -n "server"
tmux send-keys -t "$SESSION:server" \
    "cd $SERVER_DIR && docker compose up" Enter

# ── 창 4 (선택): bringup ─────────────────────────────────
if [[ "$MODE" == "sim" ]]; then
    echo "[INFO] 시뮬레이션 bringup 창 추가..."
    tmux new-window -t "$SESSION" -n "bringup"
    tmux send-keys -t "$SESSION:bringup" \
        "sleep 5 && bash $CUP_STACK_DIR/bringup_sim.sh" Enter
elif [[ "$MODE" == "real" ]]; then
    echo "[INFO] 실로봇 bringup 창 추가 (IP: $ROBOT_IP)..."
    tmux new-window -t "$SESSION" -n "bringup"
    tmux send-keys -t "$SESSION:bringup" \
        "sleep 5 && bash $CUP_STACK_DIR/bringup_real.sh $ROBOT_IP" Enter
fi

# ── 포커스 ──────────────────────────────────────────────
tmux select-window -t "$SESSION:rosbridge"

echo ""
echo "======================================================"
echo " 컵 스태킹 로봇 시스템 시작 완료"
echo "======================================================"
echo " 세션 연결:  tmux attach -t $SESSION"
echo " 창 전환:    Ctrl+b → 숫자 (0=rosbridge 1=camera 2=server)"
[[ -n "$MODE" ]] && echo "             3=bringup ($MODE)"
echo " 세션 종료:  tmux kill-session -t $SESSION"
echo ""
echo " 대시보드:   https://yarr.simplyimg.com/"
echo " API:        https://yarr-api.simplyimg.com/api/robot/status"
echo "======================================================"

tmux attach -t "$SESSION"
