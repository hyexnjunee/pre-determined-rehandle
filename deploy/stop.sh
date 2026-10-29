#!/usr/bin/env bash
# Rehandle 협업 웹 종료 (tmux / screen / nohup 어느 방식으로 띄웠든)
set -euo pipefail
SESSION="${SESSION:-rehandle}"
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PIDFILE="${ROOT}/web/data/server.pid"
stopped=0
if command -v tmux >/dev/null 2>&1 && tmux has-session -t "${SESSION}" 2>/dev/null; then
  tmux kill-session -t "${SESSION}"; echo "[=] tmux 세션 ${SESSION} 을 종료했습니다."; stopped=1
fi
if command -v screen >/dev/null 2>&1 && screen -list 2>/dev/null | grep -q "${SESSION}"; then
  screen -S "${SESSION}" -X quit || true; echo "[=] screen 세션 ${SESSION} 을 종료했습니다."; stopped=1
fi
if [ -f "${PIDFILE}" ]; then
  pid="$(cat "${PIDFILE}")"
  if kill -0 "${pid}" 2>/dev/null; then
    kill "${pid}" 2>/dev/null || true; sleep 1
    kill -0 "${pid}" 2>/dev/null && kill -9 "${pid}" 2>/dev/null || true
    echo "[=] 프로세스 ${pid} 를 종료했습니다."; stopped=1
  fi
  rm -f "${PIDFILE}"
fi
[ "${stopped}" = "0" ] && echo "[=] 실행 중인 서버가 없습니다."
exit 0
