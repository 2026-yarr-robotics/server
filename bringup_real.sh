#!/bin/bash
# DSR M0609 MoveIt Bringup - 실제 로봇 모드 (real)
# 사용법: ./bringup_real.sh [로봇IP]
# 예시:   ./bringup_real.sh 192.168.1.100

ROBOT_IP=${1:-192.168.1.100}

source /opt/ros/humble/setup.bash
[[ -f "$HOME/ws_moveit/install/setup.bash" ]] && source "$HOME/ws_moveit/install/setup.bash"
[[ -f "$HOME/ros2_ws/install/setup.bash" ]]   && source "$HOME/ros2_ws/install/setup.bash"
[[ -f "$HOME/install/setup.bash" ]]            && source "$HOME/install/setup.bash"

echo "[REAL] DSR M0609 Bringup 시작 (mode=real, host=${ROBOT_IP})"

ros2 launch dsr_bringup2 dsr_bringup2_rviz.launch.py \
    model:=m0609 \
    mode:=real \
    host:=${ROBOT_IP} \
    port:=12345
