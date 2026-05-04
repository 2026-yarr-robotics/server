#!/usr/bin/env bash
# stop_bringup.sh — bringup만 종료

echo "[INFO] bringup 종료 중..."

pkill -SIGINT -f "dsr_bringup2" 2>/dev/null || true
sleep 2
pkill -SIGKILL -f "dsr_bringup2" 2>/dev/null || true

echo "[INFO] bringup 종료 완료"
