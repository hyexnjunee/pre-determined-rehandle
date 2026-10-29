#!/usr/bin/env bash
# 웹 서버와 돌고 있는 실험(tmux exp-*)을 보여준다
#   bash deploy/status.sh
set -euo pipefail
SESSION="${SESSION:-rehandle}"
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PIDFILE="${ROOT}/web/data/server.pid"
LOG="${ROOT}/web/data/server.log"
echo "============================================================"
echo " Rehandle 협업 웹 — 상태"
echo "============================================================"
if command -v tmux >/dev/null 2>&1 && tmux has-session -t "${SESSION}" 2>/dev/null; then
  echo " 웹      : 실행 중 (tmux '${SESSION}', 콘솔: tmux attach -t ${SESSION})"
elif [ -f "${PIDFILE}" ] && kill -0 "$(cat "${PIDFILE}")" 2>/dev/null; then
  echo " 웹      : 실행 중 (nohup, PID $(cat "${PIDFILE}"))"
else
  echo " 웹      : 꺼짐  (시작: bash deploy/start.sh)"
fi
running=0
{ command -v tmux >/dev/null 2>&1 && tmux has-session -t "${SESSION}" 2>/dev/null; } && running=1
{ [ -f "${PIDFILE}" ] && kill -0 "$(cat "${PIDFILE}")" 2>/dev/null; } && running=1
if [ "${running}" = "1" ] && [ -f "${LOG}" ]; then
  url="$(grep -a '바로 열기' "${LOG}" | tail -1 || true)"
  [ -n "${url}" ] && echo " 접속    :${url#*바로 열기    :}"
fi
if command -v tmux >/dev/null 2>&1; then
  exps="$(tmux ls 2>/dev/null | grep '^exp-' | cut -d: -f1 || true)"
  if [ -n "${exps}" ]; then
    echo " 실험    :"
    for e in ${exps}; do
      name="${e#exp-}"; lg="${ROOT}/web/experiments/out/${name}.log"
      echo "   ${e}   $(tail -n 1 "${lg}" 2>/dev/null | cut -c1-110)"
    done
    echo "   (보기: tmux attach -t exp-이름 · 멈춤: tmux kill-session -t exp-이름)"
  fi
fi
echo "============================================================"
