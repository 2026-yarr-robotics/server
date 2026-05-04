#!/usr/bin/env bash
# stop_bringup.sh — bringup만 종료 (rviz2, move_group, ros2_control 포함)

SESSION="cup-stack"
BRINGUP_PATTERNS="dsr_bringup2|rviz2|move_group|ros2_control_node|robot_state_publisher|joint_state_broadcaster|spawner"

echo "[INFO] bringup 종료 중..."

# ── 1. tmux bringup 창에 Ctrl+C 전송 (launch가 하위 노드 정리) ──
if tmux has-session -t "$SESSION" 2>/dev/null; then
    BRINGUP_WIN=$(tmux list-windows -t "$SESSION" -F "#{window_index}:#{window_name}" \
        | grep "bringup" | cut -d: -f1)
    if [[ -n "$BRINGUP_WIN" ]]; then
        echo "[INFO] tmux bringup 창(#$BRINGUP_WIN)에 Ctrl+C 전송..."
        tmux send-keys -t "$SESSION:$BRINGUP_WIN" C-c ""
        sleep 3
    fi
fi

# ── 2. 남은 프로세스 SIGINT ──────────────────────────────────
if pgrep -f "$BRINGUP_PATTERNS" &>/dev/null; then
    echo "[INFO] 잔여 프로세스 SIGINT..."
    pkill -SIGINT -f "$BRINGUP_PATTERNS" 2>/dev/null || true
    sleep 2
fi

# ── 3. 강제 종료 (SIGKILL) ──────────────────────────────────
if pgrep -f "$BRINGUP_PATTERNS" &>/dev/null; then
    echo "[INFO] 강제 종료 중..."
    pkill -SIGKILL -f "$BRINGUP_PATTERNS" 2>/dev/null || true
fi

echo "[INFO] bringup 종료 완료"
