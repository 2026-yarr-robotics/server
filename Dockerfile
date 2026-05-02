FROM python:3.11-slim AS base

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    libgl1 \
    libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml ./
COPY server/ ./server/

RUN pip install --no-cache-dir -e .


FROM base AS robot

EXPOSE 8001
CMD ["cup-robot"]


FROM base AS handineye

EXPOSE 8002
CMD ["cup-handineye"]


FROM base AS handtoeye

EXPOSE 8003
CMD ["cup-handtoeye"]


FROM ros:humble AS rosbridge

RUN apt-get update && apt-get install -y --no-install-recommends \
    ros-humble-rosbridge-suite \
    && rm -rf /var/lib/apt/lists/*

CMD ["bash", "-c", \
    "source /opt/ros/humble/setup.bash && \
     ros2 launch rosbridge_server rosbridge_websocket_launch.xml"]
