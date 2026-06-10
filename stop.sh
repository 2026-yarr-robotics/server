#!/usr/bin/env bash
# stop.sh — 컵 스태킹 로봇 시스템 전체 종료 스크립트
#
# start.sh 가 tmux 세션 'cup-stack' 에 띄우는 모든 창/프로세스를 정리한다:
#   rosbridge      rosbridge_websocket
#   cam-exo/cam-hand   realsense2_camera (D435i 2대)
#   vision-exo     depth_digital_twin (world_origin/detection/point_cloud/cup_fusion)
#                  + 패널(fusion: digital_twin_panel / standalone: world_origin_control) + RViz
#   hand-fusion    (VISION_MODE=fusion_dual) hand_fusion_add.launch.py:
#                  depth_digital_twin detection/point_cloud(hand) + tf2_ros 정적 TF 2개
#   verifier       cup_stacking_verify (boxes_to_detections/verifier/topic_logger/pose_tuner) + RViz
#   bringup-agent  bringup_agent.py (포트 8099)
#   gripper        ros2 launch cup_stack gripper.launch.py
#   server         docker compose (nginx/FastAPI/cloudflared) — -d 라 세션 종료 후에도 잔존
#   agent          cup_stack_agent LLM 폐루프 노드들 + 로그 tee
# 추가로 대시보드 Bringup 버튼이 띄운 dsr_bringup2(로봇 드라이버)도 정리한다.
#
# tmux kill-session 만으로는 -d 로 분리된 Docker 컨테이너와 process-substitution
# 자식(tee)이 남으므로, 먼저 패턴별 SIGINT→SIGKILL 로 명시적으로 죽인 뒤 docker
# down, tmux kill-session 한다.

set -u
SESSION="cup-stack"
# readlink -f: 루트의 심볼릭 링크(./stop.sh)로 실행돼도 실제 server/ 경로를 잡는다.
SCRIPT_DIR=$(cd "$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")" && pwd)

echo "[INFO] 컵 스태킹 시스템 종료 중..."

# ── 1. ROS 노드 / launch / RViz 프로세스 ─────────────────
# pgrep -f 는 /proc/<pid>/cmdline 전체와 매칭한다. 설치된 ROS 2 파이썬 노드의
# cmdline 에는 .../install/<pkg>/lib/<pkg>/<exe> 형태로 패키지명이 들어가므로
# 패키지명(depth_digital_twin, cup_stacking_verify)만으로 그 패키지의 모든
# 노드를 잡는다. agent 노드는 cwd 기준 상대경로(python3 scripts/<x>.py)로 떠서
# cmdline 에 패키지명이 없어 파일명으로 따로 잡는다.
PATTERNS=(
    # 로봇 드라이버 (대시보드 Bringup 버튼으로 기동)
    "dsr_bringup2"
    # cup_stack 모션 태스크 + gripper.launch.py (ros2 launch cup_stack ...)
    # — 'cup_stacking_verify' 도 substring 으로 함께 잡히지만 아래에 명시도 한다.
    "cup_stack"
    # 넘어진 컵 복구 태스크 (대시보드 fallen-cup). cup_stack 의
    # fallen_cup_recovery/detect.launch.py 가 dsr_practice / speed_stack_yolo_seg
    # 패키지 노드로 위임하는데, 그 노드 실행파일 cmdline 은 .../ros2-cup-stack/
    # install/<pkg>/lib/<pkg>/<exe> 라 'cup_stack'(언더스코어)이 없어 위 패턴에
    # 안 잡혀 stop 후에도 잔존한다. 실행파일명으로 직접 정리한다:
    #   stand_fallen_cup(+_moveit_py) — /dsr01/*, /moveit_cpp/*, /tf 소유
    #   fallen_cup_pose_node          — /fallen_cup/*, /hand/hand/* 구독
    "stand_fallen_cup"
    "fallen_cup_pose_node"
    # vision: exo perception — depth_digital_twin 의 모든 노드(world_origin/
    # detection/point_cloud/cup_fusion)와 패널(digital_twin_panel 또는
    # world_origin_control), 그리고 digital_twin.launch.py / hand_fusion_add
    # .launch.py launch 프로세스까지 cmdline 에 'depth_digital_twin' 이 들어가
    # 한 패턴으로 모두 잡힌다.
    "depth_digital_twin"
    "digital_twin.launch.py"      # (명시용; 위 패턴에 이미 포함)
    "hand_fusion_add.launch.py"   # hand-fusion 창 launch (VISION_MODE=fusion_dual)
    # hand-fusion 의 eye-in-hand 정적 TF (handeye, world<->base_link) — tf2_ros
    # 실행파일이라 'depth_digital_twin' 패턴에 안 잡혀 별도로 정리한다.
    "static_transform_publisher"
    # vision: stack verifier (cup_stacking_verify) — 노드 + launch
    "cup_stacking_verify"
    "cup_verify.launch.py"
    # vision RViz 창 2개 (digital_twin.rviz / cup_verify.rviz)
    "rviz2"
    # RealSense 카메라 (cam-exo, cam-hand) — realsense2_camera_node 노드
    "realsense2_camera"
    # 카메라 launch 부모 (ros2 launch recode_sequence cameras_only.launch.py
    # view:=exo|hand). cmdline 에 'realsense2_camera' 가 없어 위 패턴에 안 잡혀
    # 별도로 정리한다 (안 그러면 launch 부모가 노드 종료 후에도 잔존).
    "cameras_only.launch.py"
    # rosbridge (rosbridge_websocket_launch.xml → rosbridge_websocket 노드)
    "rosbridge_websocket"
    # bringup 에이전트 (포트 8099)
    "bringup_agent.py"
    # cup_stack_agent (LLM 폐루프) 노드들 — cwd 상대경로라 파일명으로 매칭
    "fake_aggregator_node.py"
    "fake_digital_twin_node.py"
    "fake_hand_eye_node.py"
    "goal_state_publisher_node.py"
    "topic_logger_node.py"
    "llm_node.py"
    "plan_executor_node.py"
    "pick_node.py"
    # agent 노드 로그 tee (process substitution 자식; 노드 종료 후에도 남을 수 있음)
    "tee -a logs/"
)

_running=false
for _pat in "${PATTERNS[@]}"; do
    if pgrep -f -- "$_pat" &>/dev/null; then _running=true; break; fi
done

if [[ "$_running" == true ]]; then
    echo "[INFO] ROS/비전/에이전트 프로세스 SIGINT 전송..."
    for _pat in "${PATTERNS[@]}"; do
        pkill -SIGINT -f -- "$_pat" 2>/dev/null || true
    done
    sleep 3
    echo "[INFO] 남은 프로세스 SIGKILL..."
    for _pat in "${PATTERNS[@]}"; do
        pkill -SIGKILL -f -- "$_pat" 2>/dev/null || true
    done
else
    echo "[INFO] 종료할 ROS/비전/에이전트 프로세스 없음."
fi

# ── 2. Docker Compose (server 창; -d 분리 실행이라 별도 정리) ──
if docker compose -f "$SCRIPT_DIR/docker-compose.yml" ps -q 2>/dev/null | grep -q .; then
    echo "[INFO] Docker 서비스 종료..."
    docker compose -f "$SCRIPT_DIR/docker-compose.yml" down
fi

# ── 3. tmux 세션 ─────────────────────────────────────────
if tmux has-session -t "$SESSION" 2>/dev/null; then
    echo "[INFO] tmux 세션 '$SESSION' 종료..."
    tmux kill-session -t "$SESSION"
fi

echo ""
echo "======================================================"
echo " 종료 완료"
echo "======================================================"
