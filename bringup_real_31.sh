#!/bin/bash
# DSR M0609 MoveIt Bringup - 실제 로봇 모드 (real) — "31" 머신 전용
# 사용법: ./bringup_real_31.sh [로봇IP]
# 예시:   ./bringup_real_31.sh 192.168.137.100
#
# bringup_real.sh 의 실행 경로 오류 수정본.
#   기존 스크립트는 $HOME/ws_moveit, $HOME/ros2_ws, $HOME/install 만 source 했는데
#   이 머신에는 ws_moveit·$HOME/install 이 없고, 정작 colcon 으로 빌드한 프로젝트
#   워크스페이스(<repo>/ros2-cup-stack/ros2/install)를 전혀 source 하지 않아
#   프로젝트 버전의 doosan-robot2(dsr_bringup2/dsr_controller2/dsr_msgs2 등)가
#   로드되지 않았다. 아래처럼 스크립트 위치 기준으로 프로젝트 오버레이를 source 한다.

# 이 "31" 머신은 로봇을 USB 이더넷(enxec9a0c17dc1f)에 물려 쓴다. 현재 이 링크는
# 통째로 192.168.1.0/24 대역이다 (인터페이스 IP 192.168.1.50/24 + DHCP .104):
#   - 로봇  DRFL  : 192.168.1.100:12345
#   - OnRobot 그리퍼(Compute Box, Modbus TCP): 192.168.1.1:502
# 둘 다 같은 /24 라 별도 보조 IP 없이 도달된다.
#
# 192.168.1.50/24 인터페이스 주소는 NetworkManager 프로파일
# "Wired connection 2" 에 영구 추가돼 있다:
#   sudo nmcli con mod "Wired connection 2" +ipv4.addresses 192.168.1.50/24
# 일시 적용(현재 세션):  sudo ip addr add 192.168.1.50/24 dev enxec9a0c17dc1f
#
# 과거엔 로봇이 192.168.137.100 (인터페이스 192.168.137.50/24)에 있었으나
# 대역이 192.168.1.x 로 옮겨졌다. 137.x 로 잡으면 해당 대역 인터페이스가 없어
# WAN 게이트웨이로 새어나가 "connect timed out: ...:12345" 로 죽는다.
ROBOT_IP=${1:-192.168.1.100}

# 스크립트 실제 위치 — $HOME 하드코딩 대신 여기서부터 경로를 계산한다.
# readlink -f: 루트의 심볼릭 링크(./bringup_real.sh)로 실행돼도 실제 server/ 경로를 잡는다.
SCRIPT_DIR=$(cd "$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")" && pwd)

source /opt/ros/humble/setup.bash
# MoveIt 코어 베이스 (moveit_core / moveit_py / moveit_ros_planning 등)
[[ -f "$HOME/ros2_ws/install/setup.bash" ]] && source "$HOME/ros2_ws/install/setup.bash"
# 프로젝트 오버레이 — 마지막에 source 해 프로젝트 빌드본이 우선하도록 한다.
# colcon 을 ros2-cup-stack 루트에서 돌리면 install/ 이 루트에 생기고,
# ros2/ 하위에서 돌리면 ros2/install/ 에 생긴다. 둘 다 자동 탐지한다.
PROJECT_OVERLAY=""
for cand in \
    "$SCRIPT_DIR/../ros2-cup-stack/install/setup.bash" \
    "$SCRIPT_DIR/../ros2-cup-stack/ros2/install/setup.bash"; do
    if [[ -f "$cand" ]]; then
        PROJECT_OVERLAY="$cand"
        break
    fi
done
if [[ -n "$PROJECT_OVERLAY" ]]; then
    source "$PROJECT_OVERLAY"
else
    echo "[ERROR] 프로젝트 워크스페이스가 빌드되지 않았습니다 (install/setup.bash 없음):" >&2
    echo "        $SCRIPT_DIR/../ros2-cup-stack/{,ros2/}install/setup.bash" >&2
    echo "        먼저 'cd ros2-cup-stack && colcon build --symlink-install' 를 실행하세요." >&2
    exit 1
fi

# Reap orphaned child nodes too — pkill on the *.launch.py wrapper alone
# leaves ros2_control_node/robot_state_publisher/spawner/rviz2 running,
# which accumulate and make multiple controller_manager instances contend
# for the single Doosan DRFL session (/dsr01/motion/* 30s timeouts).
# Scoped to /dsr01 so unrelated ROS nodes on the host are untouched.
echo "[REAL] 기존 bringup/잔존 노드 정리 중..."
pkill -f "dsr_bringup2_moveit\.launch\.py"            2>/dev/null || true
pkill -f "dsr_bringup2_rviz\.launch\.py"              2>/dev/null || true
pkill -f "ros2_control_node.*__ns:=/dsr01"            2>/dev/null || true
pkill -f "robot_state_publisher.*__ns:=/dsr01"        2>/dev/null || true
pkill -f "controller_manager/spawner.*__ns:=/dsr01"   2>/dev/null || true
pkill -f "rviz2 .*__ns:=/dsr01"                       2>/dev/null || true
# skill_api_node(=skill_api_server)는 init 때 MoveItPy 를 controller_manager 에
# 바인딩한다. 이 bringup 이후에도 '예전' skill_api 가 살아 있으면 (start.sh 가
# bringup 보다 먼저 떴거나 병렬로 떠서) 죽은 옛 controller_manager 를 붙들고 있어
# 모든 pick/scan 이 execute 단계에서 ABORT 한다. 그래서 bringup 이 (재)시작될 때마다
# skill_api 도 함께 정리한다 — 다음 skill 호출에서 서버가 '이 bringup' 에 바인딩된
# 새 skill_api 를 lazy 재기동하므로, start.sh ↔ bringup 실행 순서·동시성과 무관하게
# 동작한다.
pkill -f "skill_api_server"                            2>/dev/null || true
pkill -f "skill_api\.launch\.py"                       2>/dev/null || true
sleep 2
pkill -9 -f "ros2_control_node.*__ns:=/dsr01"         2>/dev/null || true
pkill -9 -f "robot_state_publisher.*__ns:=/dsr01"     2>/dev/null || true
pkill -9 -f "skill_api_server"                         2>/dev/null || true
sleep 1

echo "[REAL] DSR M0609 Bringup 시작 (mode=real, host=${ROBOT_IP})"

# dsr_bringup2_rviz.launch.py only spawns joint_state_broadcaster + dsr_controller2.
# MoveIt needs dsr_moveit_controller (JTC) too; skill_api.launch.py normally
# spawns it, but if skill_api_node is already running from an earlier session,
# restarting bringup leaves the JTC unspawned and every pick aborts at step 1
# with "Action client not connected". Spawner polls for the controller_manager
# service and self-exits on activation, so it's safe to background here.
( ros2 run controller_manager spawner dsr_moveit_controller \
    --controller-manager /dsr01/controller_manager ) &

ros2 launch dsr_bringup2 dsr_bringup2_rviz.launch.py \
    model:=m0609 \
    mode:=real \
    host:=${ROBOT_IP} \
    port:=12345
