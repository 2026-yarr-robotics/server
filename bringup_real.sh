#!/bin/bash
# DSR M0609 MoveIt Bringup - 실제 로봇 모드 (real)
# 사용법: ./bringup_real.sh [로봇IP]
# 예시:   ./bringup_real.sh 192.168.1.100

ROBOT_IP=${1:-192.168.1.100}

source /opt/ros/humble/setup.bash
[[ -f "$HOME/ws_moveit/install/setup.bash" ]] && source "$HOME/ws_moveit/install/setup.bash"
[[ -f "$HOME/ros2_ws/install/setup.bash" ]]   && source "$HOME/ros2_ws/install/setup.bash"
[[ -f "$HOME/install/setup.bash" ]]            && source "$HOME/install/setup.bash"

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

ros2 launch dsr_bringup2 dsr_bringup2_rviz.launch.py \
    model:=m0609 \
    mode:=real \
    host:=${ROBOT_IP} \
    port:=12345
