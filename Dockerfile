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
