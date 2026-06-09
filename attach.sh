#!/usr/bin/env bash
# attach.sh — 이미 떠 있는 'cup-stack' tmux 세션에 '들어가기만' 한다.
#
# start.sh 는 세션을 (재)생성한다 — 기존 세션이 있으면 kill 하고 모든 창을 새로
# 띄운다. 따라서 단순히 모니터링하러 세션에 붙고 싶을 때 start.sh 를 다시 돌리면
# 안 된다(실행 중인 시스템이 통째로 재기동됨). 이 스크립트는 그냥 attach 만 한다.
#
# 사용법:
#   ./attach.sh                 # 세션에 attach (Ctrl+b → 숫자로 창 전환, Ctrl+b d 로 detach)
#   ./attach.sh <창이름>        # 특정 창으로 바로 들어가기 (server/rosbridge/agent/...)
set -e
SESSION="cup-stack"

if ! command -v tmux &>/dev/null; then
    echo "[attach] tmux 가 설치돼 있지 않습니다." >&2
    exit 1
fi

if ! tmux has-session -t "$SESSION" 2>/dev/null; then
    echo "[attach] tmux 세션 '$SESSION' 이 없습니다 — 먼저 ./start.sh 로 기동하세요." >&2
    exit 1
fi

# 인자로 창 이름을 주면 그 창을 선택한 뒤 attach.
if [[ -n "${1:-}" ]]; then
    tmux select-window -t "$SESSION:$1" 2>/dev/null \
        || echo "[attach] 창 '$1' 없음 — 현재 창으로 attach." >&2
fi

exec tmux attach -t "$SESSION"
