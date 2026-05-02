#!/usr/bin/env bash
set -e

ROS_SETUP="/opt/ros/humble/setup.bash"

if [[ ! -f "$ROS_SETUP" ]]; then
    echo "ERROR: ROS 2 Humble not found at $ROS_SETUP" >&2
    exit 1
fi

source "$ROS_SETUP"

if ! ros2 pkg list 2>/dev/null | grep -q "^rosbridge_server$"; then
    echo "rosbridge_server not found. Installing..."
    sudo apt-get update -qq
    sudo apt-get install -y ros-humble-rosbridge-suite
    source "$ROS_SETUP"
fi

echo "Starting rosbridge_server on port 9090..."
exec ros2 launch rosbridge_server rosbridge_websocket_launch.xml
