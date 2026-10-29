#!/usr/bin/env bash
# ============================================================
# [서버] GitHub 의 최신 코드를 받고, 웹이 켜져 있으면 다시 띄운다
#   bash deploy/update.sh
# 돌고 있는 실험(tmux exp-*)은 건드리지 않는다 (시작할 때의 코드로 끝까지 감)
# ============================================================
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SESSION="${SESSION:-rehandle}"
cd "${ROOT}"
before="$(git rev-parse --short HEAD)"
git pull --ff-only
after="$(git rev-parse --short HEAD)"
if [ "${before}" = "${after}" ]; then
  echo "[=] 이미 최신입니다 (${after})."
else
  echo "[+] ${before} → ${after}"; git log --oneline "${before}..${after}" | head -n 20
fi
running=0
{ command -v tmux >/dev/null 2>&1 && tmux has-session -t "${SESSION}" 2>/dev/null; } && running=1
{ [ -f web/data/server.pid ] && kill -0 "$(cat web/data/server.pid)" 2>/dev/null; } && running=1
if [ "${running}" = "1" ] && [ "${before}" != "${after}" ]; then
  echo "[+] 웹 다시 띄우기"; bash deploy/stop.sh; bash deploy/start.sh | tail -n 6
fi
exps="$(tmux ls 2>/dev/null | grep '^exp-' | cut -d: -f1 || true)"
[ -n "${exps}" ] && echo "[=] 돌고 있는 실험은 그대로 둡니다: ${exps}"
exit 0
