#!/usr/bin/env bash
# start.sh — 컵 스태킹 로봇 시스템 통합 실행 스크립트
#
# 사용법:
#   ./start.sh                     # rosbridge + exo/hand camera + vision + bringup-agent + Docker
#   WITH_HAND_CAM=false ./start.sh # hand 카메라 끄기 (USB 충돌 시)
#   # cup_stack_agent is started manually from ../cup_stack_agent
#
# bringup은 웹 대시보드(https://yarr.simplyimg.com)에서 버튼으로 제어합니다.

set -e

ROS_SETUP="/opt/ros/humble/setup.bash"
# readlink -f 로 심볼릭 링크(루트의 ./start.sh)를 실제 server/ 경로로 resolve한다.
# 안 하면 링크로 실행 시 SCRIPT_DIR 이 repo 루트가 돼 ../<submodule> 경로가 어긋난다.
SCRIPT_DIR=$(cd "$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")" && pwd)
SESSION="cup-stack"

# 모든 ROS 노드가 같은 도메인에서 통신하도록 일관 적용한다 (.bashrc 와 동일값).
# 이 export 는 tmux 서버가 상속하므로 아래 모든 창의 셸에 전파된다.
export ROS_DOMAIN_ID="${ROS_DOMAIN_ID:-21}"
# All ROS nodes run on this host; keep DDS discovery off Docker/Tailscale/robot NICs.
export ROS_LOCALHOST_ONLY=1

# hand 카메라 기본 기동(on). 단 같은 호스트에서 D435i 2대(exo+hand)가 USB
# 자원을 다투면 "resource busy"/SIGSEGV 가 날 수 있으므로, 문제 시
# WITH_HAND_CAM=false ./start.sh 로 끈다.
WITH_HAND_CAM="${WITH_HAND_CAM:-true}"

# exo perception 의 RViz. world_origin_node 가 ArUco(ID 0)로 world 좌표계를
# 잡는데, RViz 로 world 축이 로봇 base 와 맞는지 확인하고 Redetect 팝업으로
# 재검출하는 초기화 작업이 필요하므로 기본 on. headless 면 VISION_RVIZ=false.
VISION_RVIZ="${VISION_RVIZ:-true}"
# 비전 파이프라인 모드. standalone = 단일 exo 카메라(검증된 production 9Hz
# baseline). fusion = exo point_cloud를 producer 로 돌리고 cup_fusion_node 가
# /digital_twin/boxes + /vision/cups_on_table 를 소유(검증된 exo-only 색계약).
# hand dual-cam 은 eye-in-hand 캘리브/TF dedup 선결이라 여기 미포함.
VISION_MODE="${VISION_MODE:-fusion}"
# standalone | fusion(exo-only producer+cup_fusion) | fusion_dual(exo+hand)
case "$VISION_MODE" in
  fusion)      VISION_FUSION=true;  VISION_WITH_HAND=false ;;
  fusion_dual) VISION_FUSION=true;  VISION_WITH_HAND=true  ;;
  *)           VISION_FUSION=false; VISION_WITH_HAND=false ;;
esac
# eye-in-hand world<->base_link yaw 보정 노브(도). RViz 에서 hand 컵이 exo 컵과
# 안 겹치면 90 / -90 / 180 으로 바꿔 재실행.
VISION_WORLD_BASE_YAW="${VISION_WORLD_BASE_YAW:-0}"

# cup_stack_agent is no longer launched from this script. Start it manually
# from ../cup_stack_agent when you want to run the LLM loop. WITH_AGENT is
# kept only to warn old commands that still set it.
WITH_AGENT="${WITH_AGENT:-false}"

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

# ── 정본 vision 패키지 빌드 (변경 시에만) ───────────────────────────────────
# 이 스크립트가 source 하는 install/ 은 colcon 산출물이다. --symlink-install 덕에
# 파이썬 '소스'(.py) 수정은 재빌드 없이 즉시 반영되지만, params.yaml 같은 config 는
# build/install 로 '복사'되므로 재빌드해야 반영된다(= "고쳤는데 안 먹는" 문제).
# 그래서 매번 빌드하지 않고, 워크스페이스별로 src/ 가 마지막 빌드 이후 바뀐 경우에만
# colcon build 한다 (install/.last_build 스탬프와 mtime 비교; install/ 은 gitignore).
#   - SKIP_BUILD=true  → 변경이 있어도 전부 생략(가장 빠른 재기동).
#   - FORCE_BUILD=true → 변경 여부 무관 전부 재빌드.
#   - ros2-cup-stack(로봇 스택)은 변경이 드물어 자동 빌드 대상에서 제외 — 바뀌면
#     수동으로 'cd ../ros2-cup-stack && colcon build --symlink-install'.
vision_ws_needs_build() {
    local ws="$1"
    local stamp="$ws/install/.last_build"
    [[ -f "$ws/install/setup.bash" ]] || return 0   # 한 번도 빌드 안 됨 → 빌드
    [[ -f "$stamp" ]] || return 0                    # 스탬프 없음 → 한 번 빌드해 기준 생성
    # src/ 아래에 스탬프보다 새 파일이 하나라도 있으면 재빌드 (-quit: 첫 매치서 종료)
    [[ -n "$(find "$ws/src" -type f -newer "$stamp" -print -quit 2>/dev/null)" ]]
}

if [[ "${SKIP_BUILD:-false}" == "true" ]]; then
    echo "[INFO] SKIP_BUILD=true → vision 빌드 전부 생략."
else
    echo "[INFO] 정본 vision 패키지 점검 중 (변경 시에만 빌드; FORCE_BUILD=true 강제, SKIP_BUILD=true 생략)..."
    # shellcheck disable=SC1090
    source "$ROS_SETUP"
    for ws in \
        "$SCRIPT_DIR/../ros2-depth-point-cloude" \
        "$SCRIPT_DIR/../vision-node"; do
        if [[ "${FORCE_BUILD:-false}" == "true" ]] || vision_ws_needs_build "$ws"; then
            echo "  - colcon build: $ws"
            if ! ( cd "$ws" && colcon build --symlink-install ); then
                echo "[ERROR] colcon build 실패: $ws" >&2
                exit 1
            fi
            touch "$ws/install/.last_build"
        else
            echo "  - 변경 없음 → 빌드 생략: $ws"
        fi
    done
    echo "[INFO] vision 빌드 점검 완료."
fi

# ── ros2-cup-stack(로봇 스택: cup_stack / dsr_practice / doosan / interfaces) ──
# 빌드 산출물(install)이 '없을 때만' 부트스트랩으로 한 번 빌드한다. gripper·
# skill_api·fallen_cup recovery 가 이 install 에서 패키지를 찾으므로, 한 번도
# 빌드 안 된 체크아웃이면 여기서 만들어 준다. 변경이 드물어 매번 빌드하진 않는다.
#   - 소스를 고쳤는데 반영이 필요하면(예: scan/fallen_cup 동작 변경) 수동으로
#     'cd ../ros2-cup-stack && colcon build --packages-select <pkg>' 후 재실행.
#   - FORCE_BUILD=true 면 install 이 있어도 강제로 다시 빌드한다.
ROS2_CUP_STACK_DIR="$SCRIPT_DIR/../ros2-cup-stack"
if [[ "${SKIP_BUILD:-false}" != "true" ]]; then
    if [[ "${FORCE_BUILD:-false}" == "true" || ! -f "$ROS2_CUP_STACK_DIR/install/setup.bash" ]]; then
        echo "[INFO] ros2-cup-stack 빌드 중 (install 없음 또는 FORCE_BUILD)..."
        # shellcheck disable=SC1090
        source "$ROS_SETUP"
        if ! ( cd "$ROS2_CUP_STACK_DIR" && colcon build --symlink-install ); then
            echo "[ERROR] ros2-cup-stack colcon build 실패" >&2
            exit 1
        fi
        echo "[INFO] ros2-cup-stack 빌드 완료."
    else
        echo "[INFO] ros2-cup-stack 이미 빌드됨 → 생략 (소스 변경 반영은 수동 colcon build)."
    fi
fi

# ── 기존 세션 정리 ────────────────────────────────────────
if tmux has-session -t "$SESSION" 2>/dev/null; then
    echo "[INFO] 기존 tmux 세션 '$SESSION' 종료 중..."
    tmux kill-session -t "$SESSION"
fi

echo "[INFO] tmux 세션 '$SESSION' 시작..."
tmux new-session -d -s "$SESSION" -x 220 -y 50 -n "server"

# ── 창 1: Docker 서버 (nginx + FastAPI + cloudflared) ────
# -d 로 컨테이너를 분리 실행 → tmux 세션이 종료돼도 컨테이너가 유지됨.
# 첫 창(#1)으로 둬서 attach 시 바로 서버 로그가 보인다.
tmux send-keys -t "$SESSION:server" \
    "cd $SCRIPT_DIR && docker compose up -d && docker compose logs -f" Enter

# ── 창 rosbridge ──────────────────────────────────────────
tmux new-window -t "$SESSION" -n "rosbridge"
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
CUP_STACK_SETUP="$SCRIPT_DIR/../ros2-cup-stack/install/setup.bash"
# cameras_only.launch.py 와 cameras.yaml 은 recode_sequence 패키지 share 에
# 있다. recode_sequence 패키지는 ros2-depth-point-cloude 워크스페이스에 병합되어 있으므로
# (구 cup-stack-server/yarr-robust-speed-stack 아님) 그쪽 install 을 source 한다.
RECODE_SETUP="$SCRIPT_DIR/../ros2-depth-point-cloude/install/setup.bash"
tmux send-keys -t "$SESSION:cam-exo" \
    "source $ROS_SETUP && source $RECODE_SETUP && source $CUP_STACK_SETUP && ros2 launch recode_sequence cameras_only.launch.py view:=exo" Enter

# ── 창: exo perception (depth_digital_twin) ───────────────
# exo 카메라 영상(/exo/exo/*)을 받아 /digital_twin/boxes, /vision/cups_on_table
# 를 만드는 비전 파이프라인. cup_stack_agent 의 stabilizer/aggregator 가 이걸
# 소비한다. camera_ns:=exo 로 /camera/camera/* → /exo/exo/* 리맵이 걸린다.
# integration repo 의 ros2-depth-point-cloude install 을 반드시 source.
DEPTH_DT_SETUP="$SCRIPT_DIR/../ros2-depth-point-cloude/install/setup.bash"
tmux new-window -t "$SESSION" -n "vision-exo"
# 카메라가 /exo/exo/* 발행을 시작할 시간을 준 뒤 파이프라인을 띄운다
# (world_origin_node 의 ArUco 타임아웃이 카메라 부팅 전에 도는 것 방지).
tmux send-keys -t "$SESSION:vision-exo" \
    "source $ROS_SETUP && source $DEPTH_DT_SETUP && sleep 8 && ros2 launch depth_digital_twin digital_twin.launch.py camera_ns:=exo rviz:=$VISION_RVIZ control_panel:=false fusion:=$VISION_FUSION" Enter

# ── 창: stack verifier (cup_stacking_verify) ──────────────
# /digital_twin/boxes 를 받아 어느 슬롯이 채워졌는지 판정해 /vision/stack(+
# /stack_track_ids)을 발행한다. aggregator 가 /vision/stack -> /stack 으로 중계해
# GSP 가 각 pyramid step 완료를 확인하고 다음 step 으로 진행한다(이게 없으면
# step 1 에서 루프가 멈춤). slot 단축키(L1_L..)는 payload_builder.normalize_stack
# 가 L1_left.. 로 변환하므로 그대로 둔다.
VISION_NODE_SETUP="$SCRIPT_DIR/../vision-node/install/setup.bash"
tmux new-window -t "$SESSION" -n "verifier"
# vision-exo 가 /digital_twin/boxes 를 내보낸 뒤 띄운다.
tmux send-keys -t "$SESSION:verifier" \
    "source $ROS_SETUP && source $VISION_NODE_SETUP && sleep 12 && ros2 launch cup_stacking_verify cup_verify.launch.py rviz:=$VISION_RVIZ tuner:=false use_test_pub:=false cp_z:=0.095" Enter

# ── 창: hand-fusion (VISION_MODE=fusion_dual 일 때만) ──────
# hand 카메라 producer + eye-in-hand 정적 TF(handeye, world<->base_link)를 띄워
# cup_fusion 이 exo+hand 를 융합한다. robot_state_publisher 를 따로 안 띄우고 dsr
# 의 live TF 를 재사용 → TF 충돌 없음. yaw 는 VISION_WORLD_BASE_YAW 노브.
if [[ "$VISION_WITH_HAND" == "true" ]]; then
    tmux new-window -t "$SESSION" -n "hand-fusion"
    tmux send-keys -t "$SESSION:hand-fusion" \
        "source $ROS_SETUP && source $DEPTH_DT_SETUP && sleep 16 && ros2 launch depth_digital_twin hand_fusion_add.launch.py view:=exo world_base_yaw_deg:=$VISION_WORLD_BASE_YAW" Enter
fi

# hand 카메라는 이번 exo-only 실험에서 기본 미기동 (WITH_HAND_CAM=true 일 때만).
if [[ "$WITH_HAND_CAM" == "true" ]]; then
    tmux new-window -t "$SESSION" -n "cam-hand"
    tmux send-keys -t "$SESSION:cam-hand" \
        "source $ROS_SETUP && source $RECODE_SETUP && source $CUP_STACK_SETUP && ros2 launch recode_sequence cameras_only.launch.py view:=hand" Enter
fi

# ── 창 2: bringup 에이전트 (포트 8099) ────────────────────
tmux new-window -t "$SESSION" -n "bringup-agent"
tmux send-keys -t "$SESSION:bringup-agent" \
    "python3 $SCRIPT_DIR/bringup_agent.py" Enter

# ── 창 3: 그리퍼 노드 ────────────────────────────────────
DOOSAN_SETUP="$HOME/ros2_ws/install/setup.bash"
ROS2_CUP_STACK_SETUP="$SCRIPT_DIR/../ros2-cup-stack/install/setup.bash"
tmux new-window -t "$SESSION" -n "gripper"
tmux send-keys -t "$SESSION:gripper" \
    "source $ROS_SETUP && source $DOOSAN_SETUP && source $ROS2_CUP_STACK_SETUP && ros2 launch cup_stack gripper.launch.py" Enter

# (Docker 서버 창은 창 #1 로 이동했다 — 세션 생성부 참고.)

# ── cup_stack_agent ─────────────────────────────────────
# Do not launch cup_stack_agent/start.sh from server/start.sh. Start it
# manually from ../cup_stack_agent when you want to run the LLM loop.
if [[ "$WITH_AGENT" == "true" ]]; then
    echo "[WARN] WITH_AGENT=true is ignored; start cup_stack_agent/start.sh manually."
fi

# ── 포커스 ──────────────────────────────────────────────
tmux select-window -t "$SESSION:server"

echo ""
echo "======================================================"
echo " 컵 스태킹 로봇 시스템 시작 완료"
echo "======================================================"
echo " 세션 연결:   tmux attach -t $SESSION"
echo " 창 전환:     Ctrl+b → 숫자 (또는 창 이름)"
echo "   rosbridge / cam-exo (eye-to-hand) / vision-exo (depth_digital_twin)"
echo "   verifier (/stack 판정) / bringup-agent (port 8099) / gripper / server (Docker)"
if [[ "$WITH_HAND_CAM" == "true" ]]; then
    echo "   cam-hand (eye-in-hand)  ← 기본 기동 (끄려면 WITH_HAND_CAM=false ./start.sh)"
else
    echo "   cam-hand 는 미기동 (WITH_HAND_CAM=false 로 꺼짐)"
fi
echo "   agent 는 자동 기동하지 않음 (cup_stack_agent/start.sh 를 별도 실행)"
echo " ROS_DOMAIN_ID=${ROS_DOMAIN_ID}"
echo " 세션 종료:   tmux kill-session -t $SESSION"
echo ""
echo " 대시보드:    https://yarr.simplyimg.com"
echo " API:         https://yarr-api.simplyimg.com/api/robot/status"
echo " Bringup 제어: 대시보드 헤더의 Bringup 버튼 사용"
echo "======================================================"

tmux attach -t "$SESSION"
