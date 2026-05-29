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

# 이 "31" 머신은 로봇을 USB 이더넷(enxec9a0c17dc1f, 192.168.137.50/24)에 물려 쓴다.
# 로봇은 같은 대역의 192.168.137.100 (DRFL :12345). 192.168.1.100 은 이 머신에
# 해당 대역 인터페이스가 없어 WAN 게이트웨이로 새어나가 연결이 타임아웃된다.
ROBOT_IP=${1:-192.168.137.100}

# 스크립트 실제 위치 — $HOME 하드코딩 대신 여기서부터 경로를 계산한다.
SCRIPT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)

source /opt/ros/humble/setup.bash
# MoveIt 코어 베이스 (moveit_core / moveit_py / moveit_ros_planning 등)
[[ -f "$HOME/ros2_ws/install/setup.bash" ]] && source "$HOME/ros2_ws/install/setup.bash"
# 프로젝트 오버레이 — 마지막에 source 해 프로젝트 빌드본이 우선하도록 한다.
PROJECT_OVERLAY="$SCRIPT_DIR/../ros2-cup-stack/ros2/install/setup.bash"
if [[ -f "$PROJECT_OVERLAY" ]]; then
    source "$PROJECT_OVERLAY"
else
    echo "[ERROR] 프로젝트 워크스페이스가 빌드되지 않았습니다: $PROJECT_OVERLAY" >&2
    echo "        먼저 'cd ros2-cup-stack/ros2 && colcon build --symlink-install' 를 실행하세요." >&2
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
sleep 2
pkill -9 -f "ros2_control_node.*__ns:=/dsr01"         2>/dev/null || true
pkill -9 -f "robot_state_publisher.*__ns:=/dsr01"     2>/dev/null || true
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
