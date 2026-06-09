#!/usr/bin/env bash
# stop.sh — 컵 스태킹 로봇 시스템 전체 종료 스크립트

SESSION="cup-stack"
# readlink -f: 루트의 심볼릭 링크(./stop.sh)로 실행돼도 실제 server/ 경로를 잡는다.
SCRIPT_DIR=$(cd "$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")" && pwd)

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

# ── 1-3. cup_stack_agent (LLM 폐루프 실험 노드) ──────────
# start.sh 가 'agent' 창에서 함께 띄운 노드들. 노드 명령줄에 'cup_stack' 문자열이
# 없어(예: python3 scripts/llm_node.py) 위 cup_stack pkill 로 잡히지 않으므로 별도
# 정리한다. tmux 세션 종료로도 정리되지만 tee/process-substitution 자식이 남을 수
# 있어 명시적으로 SIGINT→SIGKILL 한다.
AGENT_PATTERNS=(
    "cup_stack_agent/start.sh"
    "fake_aggregator_node.py"
    "fake_digital_twin_node.py"
    "fake_hand_eye_node.py"
    "goal_state_publisher_node.py"
    "topic_logger_node.py"
    "llm_node.py"
    "plan_executor_node.py"
    "pick_node.py"
)
_agent_running=false
for _pat in "${AGENT_PATTERNS[@]}"; do
    if pgrep -f "$_pat" &>/dev/null; then _agent_running=true; break; fi
done
if [[ "$_agent_running" == true ]]; then
    echo "[INFO] cup_stack_agent 노드 종료..."
    for _pat in "${AGENT_PATTERNS[@]}"; do
        pkill -SIGINT -f "$_pat" 2>/dev/null || true
    done
    sleep 2
    for _pat in "${AGENT_PATTERNS[@]}"; do
        pkill -SIGKILL -f "$_pat" 2>/dev/null || true
    done
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
