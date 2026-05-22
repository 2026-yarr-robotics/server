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
tmux new-session -d -s "$SESSION" -x 220 -y 50 -n "rosbridge"

# ── 창 rosbridge ──────────────────────────────────────────
tmux send-keys -t "$SESSION:rosbridge" \
    "bash $SCRIPT_DIR/rosbridge.sh" Enter

# ── 창 1: RealSense 카메라 (시리얼별 2대 분리) ────────────
# exo  = eye-to-hand  (고정/외부 카메라, serial 242322077444)  → 토픽 /exo/exo/*
# hand = eye-in-hand  (그리퍼 장착 카메라, serial 140122076335) → 토픽 /hand/hand/*
# serial_no 는 realsense2_camera 권장 표기인 '_' 접두사 형식을 사용한다.
tmux new-window -t "$SESSION" -n "cam-exo"
# 카메라는 cup_stack 패키지의 cameras_only.launch.py 를 사용한다.
# serial→role 매핑은 cup_stack/config/cameras.yaml 에서 관리되며,
# view:=exo|hand 로 카메라 1대씩 분리 기동해 D435i 두 대가 USB 자원을
# 두고 충돌(SIGSEGV)하는 것을 막는다. IMU/initial_reset 비활성, 안정 동작.
# 해상도는 launch 파일 default (color/depth 1280x720x30) 를 사용한다.
CUP_STACK_SETUP="$SCRIPT_DIR/../ros2-cup-stack/ros2/install/setup.bash"
# launch 는 ros2-recode-sequence 의 cameras_only.launch.py 를 그대로
# 가져온 hard copy. cameras.yaml 경로를 recode_sequence share 에서
# 찾으므로 그 워크스페이스도 함께 source 한다.
RECODE_SETUP="$HOME/Projects/ros2-recode-sequence/install/setup.bash"
tmux send-keys -t "$SESSION:cam-exo" \
    "source $ROS_SETUP && source $RECODE_SETUP && source $CUP_STACK_SETUP && ros2 launch cup_stack cameras_only.launch.py view:=exo" Enter

tmux new-window -t "$SESSION" -n "cam-hand"
tmux send-keys -t "$SESSION:cam-hand" \
    "source $ROS_SETUP && source $RECODE_SETUP && source $CUP_STACK_SETUP && ros2 launch cup_stack cameras_only.launch.py view:=hand" Enter

# ── 창 2: bringup 에이전트 (포트 8099) ────────────────────
tmux new-window -t "$SESSION" -n "bringup-agent"
tmux send-keys -t "$SESSION:bringup-agent" \
    "python3 $SCRIPT_DIR/bringup_agent.py" Enter

# ── 창 3: 그리퍼 노드 ────────────────────────────────────
DOOSAN_SETUP="$HOME/ros2_ws/install/setup.bash"
ROS2_CUP_STACK_SETUP="$SCRIPT_DIR/../ros2-cup-stack/ros2/install/setup.bash"
tmux new-window -t "$SESSION" -n "gripper"
tmux send-keys -t "$SESSION:gripper" \
    "source $ROS_SETUP && source $DOOSAN_SETUP && source $ROS2_CUP_STACK_SETUP && ros2 launch cup_stack gripper.launch.py" Enter

# ── 창 4: Docker 서버 (nginx + FastAPI + cloudflared) ────
# -d 로 컨테이너를 분리 실행 → tmux 세션이 종료돼도 컨테이너가 유지됨
tmux new-window -t "$SESSION" -n "server"
tmux send-keys -t "$SESSION:server" \
    "cd $SCRIPT_DIR && docker compose up -d && docker compose logs -f" Enter

# ── 포커스 ──────────────────────────────────────────────
tmux select-window -t "$SESSION:rosbridge"

echo ""
echo "======================================================"
echo " 컵 스태킹 로봇 시스템 시작 완료"
echo "======================================================"
echo " 세션 연결:   tmux attach -t $SESSION"
echo " 창 전환:     Ctrl+b → 숫자"
echo "   1 = rosbridge   2 = cam-exo (eye-to-hand)   3 = cam-hand (eye-in-hand)"
echo "   4 = bringup-agent (port 8099)"
echo "   5 = gripper"
echo "   6 = server (Docker)"
echo " 세션 종료:   tmux kill-session -t $SESSION"
echo ""
echo " 대시보드:    https://yarr.simplyimg.com"
echo " API:         https://yarr-api.simplyimg.com/api/robot/status"
echo " Bringup 제어: 대시보드 헤더의 Bringup 버튼 사용"
echo "======================================================"

tmux attach -t "$SESSION"
