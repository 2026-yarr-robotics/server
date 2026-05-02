#!/usr/bin/env bash
set -e

ROS_SETUP="/opt/ros/humble/setup.bash"

if [[ ! -f "$ROS_SETUP" ]]; then
    echo "ERROR: ROS 2 Humble not found at $ROS_SETUP" >&2
    exit 1
fi

source "$ROS_SETUP"

echo "Starting rosbridge_server on port 9090..."
exec ros2 launch rosbridge_server rosbridge_websocket_launch.xml
