#!/bin/bash
# DSR M0609 MoveIt Bringup - 실제 로봇 모드 (real)
# 사용법: ./bringup_real.sh [로봇IP]
# 예시:   ./bringup_real.sh 192.168.1.100

ROBOT_IP=${1:-192.168.1.100}

source /opt/ros/humble/setup.bash
source /home/ssu/ws_moveit/install/setup.bash
source /home/ssu/ros2_ws/install/setup.bash
source /home/ssu/install/setup.bash

echo "[REAL] DSR M0609 MoveIt Bringup 시작 (mode=real, host=${ROBOT_IP})"

ros2 launch dsr_bringup2 dsr_bringup2_moveit.launch.py \
    model:=m0609 \
    mode:=real \
    host:=${ROBOT_IP} \
    port:=12345
