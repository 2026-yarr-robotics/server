#!/usr/bin/env bash
set -e

ROS_SETUP="/opt/ros/humble/setup.bash"
SCRIPT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
CUP_STACK_ROOT=$(cd "$SCRIPT_DIR/.." && pwd)
DOOSAN_SETUP="$HOME/ros2_ws/install/setup.bash"
ROS2_CUP_STACK_SETUP="$CUP_STACK_ROOT/ros2-cup-stack/ros2/install/setup.bash"

if [[ ! -f "$ROS_SETUP" ]]; then
    echo "ERROR: ROS 2 Humble not found at $ROS_SETUP" >&2
    exit 1
fi

source "$ROS_SETUP"

if [[ -f "$DOOSAN_SETUP" ]]; then
    source "$DOOSAN_SETUP"
else
    echo "WARN: Doosan workspace not found at $DOOSAN_SETUP" >&2
fi
if [[ -f "$ROS2_CUP_STACK_SETUP" ]]; then
    source "$ROS2_CUP_STACK_SETUP"
else
    echo "WARN: ROS 2 cup-stack overlay not found at $ROS2_CUP_STACK_SETUP" >&2
fi

if ! ros2 pkg list 2>/dev/null | grep -q "^rosbridge_server$"; then
    echo "rosbridge_server not found. Installing..."
    sudo apt-get update -qq
    sudo apt-get install -y ros-humble-rosbridge-suite
    source "$ROS_SETUP"
    if [[ -f "$CUP_STACK_SETUP" ]]; then
        source "$CUP_STACK_SETUP"
    fi
    if [[ -f "$ROS2_CUP_STACK_SETUP" ]]; then
        source "$ROS2_CUP_STACK_SETUP"
    fi
fi

echo "Starting rosbridge_server on port 9090..."
exec ros2 launch rosbridge_server rosbridge_websocket_launch.xml \
    call_services_in_new_thread:=true
