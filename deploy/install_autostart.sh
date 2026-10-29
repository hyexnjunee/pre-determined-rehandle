#!/usr/bin/env bash
# 서버 재부팅 후에도 웹이 자동으로 뜨도록 등록 (sudo 불필요, 개인 crontab @reboot)
#   bash deploy/install_autostart.sh          # 등록
#   bash deploy/install_autostart.sh --remove # 해제
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PORT="${PORT:-8766}"; SHARE="${SHARE:-1}"; ENV_NAME="${ENV_NAME:-opt_env}"
MARKER="# rehandle-web autostart"
CRON_LINE="@reboot PORT=${PORT} SHARE=${SHARE} ENV_NAME=${ENV_NAME} /bin/bash ${ROOT}/deploy/start.sh >> ${ROOT}/web/data/autostart.log 2>&1 ${MARKER}"
current="$(crontab -l 2>/dev/null || true)"
cleaned="$(printf '%s\n' "${current}" | grep -v -F "${MARKER}" || true)"
if [ "${1:-}" = "--remove" ]; then
  printf '%s\n' "${cleaned}" | grep -v '^$' | crontab - 2>/dev/null || crontab -r 2>/dev/null || true
  echo "[=] 자동 시작 등록을 해제했습니다."; exit 0
fi
{ printf '%s\n' "${cleaned}" | grep -v '^$' || true; echo "${CRON_LINE}"; } | crontab -
echo "[=] 등록 완료 (포트 ${PORT}). 해제: bash deploy/install_autostart.sh --remove"
crontab -l | grep -F "${MARKER}" || true
