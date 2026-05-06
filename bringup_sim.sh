#!/bin/bash
# DSR M0609 MoveIt Bringup - 시뮬레이션 모드 (virtual)

source /opt/ros/humble/setup.bash
source /home/ssu/ws_moveit/install/setup.bash
source /home/ssu/ros2_ws/install/setup.bash
source /home/ssu/install/setup.bash

echo "[SIM] DSR M0609 MoveIt Bringup 시작 (mode=virtual)"

ros2 launch dsr_bringup2 dsr_bringup2_moveit.launch.py \
    model:=m0609 \
    mode:=virtual \
    host:=127.0.0.1 \
    port:=12345
