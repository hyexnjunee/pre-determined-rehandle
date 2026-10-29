#!/usr/bin/env bash
# ============================================================
# Rehandle 협업 웹을 백그라운드로 띄운다 (SSH 를 끊어도 계속 실행)
#
#   bash deploy/start.sh              # 내부망 공개 + 토큰 (포트 8766)
#   PORT=8800 bash deploy/start.sh    # 포트 지정
#   SHARE=0 bash deploy/start.sh      # 나만 (127.0.0.1, VS Code 포트 포워딩으로 열기)
#
# crane-bench(8765)와 같은 서버에서 같이 돌 수 있게 세션 이름 · 포트를 따로 쓴다.
# 실행 방식은 있는 것을 자동으로 고른다: tmux → screen → nohup
# 종료: bash deploy/stop.sh
# ============================================================
set -euo pipefail

SESSION="${SESSION:-rehandle}"
PORT="${PORT:-8766}"
SHARE="${SHARE:-1}"
ENV_NAME="${ENV_NAME:-opt_env}"
BACKEND="${BACKEND:-auto}"
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
WEB="${ROOT}/web"
LOG="${WEB}/data/server.log"
PIDFILE="${WEB}/data/server.pid"

mkdir -p "${WEB}/data"

if [ "${BACKEND}" = "auto" ]; then
  if command -v tmux >/dev/null 2>&1; then BACKEND="tmux"
  elif command -v screen >/dev/null 2>&1; then BACKEND="screen"
  else BACKEND="nohup"; fi
fi

already_running() {
  if command -v tmux >/dev/null 2>&1 && tmux has-session -t "${SESSION}" 2>/dev/null; then echo "tmux"; return 0; fi
  if command -v screen >/dev/null 2>&1 && screen -list 2>/dev/null | grep -q "${SESSION}"; then echo "screen"; return 0; fi
  if [ -f "${PIDFILE}" ] && kill -0 "$(cat "${PIDFILE}")" 2>/dev/null; then echo "nohup"; return 0; fi
  return 1
}
if running_as="$(already_running)"; then
  echo "[=] 이미 실행 중입니다 (${running_as}). 먼저 'bash deploy/stop.sh' 로 종료하세요."
  exit 0
fi

CONDA_BASE="${CONDA_BASE:-$(conda info --base 2>/dev/null || true)}"
if [ -z "${CONDA_BASE}" ]; then
  for c in "$HOME/miniconda3" "$HOME/anaconda3" "$HOME/miniforge3" "/opt/conda"; do
    [ -f "$c/etc/profile.d/conda.sh" ] && CONDA_BASE="$c" && break
  done
fi
if [ -z "${CONDA_BASE}" ] || [ ! -f "${CONDA_BASE}/etc/profile.d/conda.sh" ]; then
  echo "[!] conda 를 찾지 못했습니다. CONDA_BASE=/경로/to/miniconda3 bash deploy/start.sh 로 지정하세요."
  exit 1
fi

ARGS="--port ${PORT}"
[ "${SHARE}" = "1" ] && ARGS="${ARGS} --share"
# 원본 데이터가 서버에 있으면 실데이터 가져오기도 쓸 수 있다 (없어도 웹은 뜸)
[ -d "${ROOT}/data" ] && ARGS="${ARGS} --realdata ${ROOT}/data"

ENV_PREFIX="PYTHONUNBUFFERED=1 OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 MKL_NUM_THREADS=1"
LAUNCH="source ${CONDA_BASE}/etc/profile.d/conda.sh && conda activate ${ENV_NAME} && \
        cd ${WEB} && ${ENV_PREFIX} exec python run_server.py ${ARGS} --no-browser"

case "${BACKEND}" in
  tmux)   tmux new-session -d -s "${SESSION}" -c "${WEB}" "${LAUNCH} 2>&1 | tee -a ${LOG}" ;;
  screen) screen -dmS "${SESSION}" bash -c "${LAUNCH} 2>&1 | tee -a ${LOG}" ;;
  nohup)  nohup bash -c "${LAUNCH}" >> "${LOG}" 2>&1 < /dev/null & echo $! > "${PIDFILE}" ;;
  *) echo "[!] 알 수 없는 BACKEND: ${BACKEND} (tmux / screen / nohup)"; exit 1 ;;
esac

sleep 2
echo "============================================================"
if [ "${BACKEND}" = "tmux" ]; then tmux capture-pane -p -t "${SESSION}" | tail -n 25; else tail -n 25 "${LOG}"; fi
echo "============================================================"
echo "실행 방식: ${BACKEND}"
case "${BACKEND}" in
  tmux)   echo "콘솔 보기: tmux attach -t ${SESSION}   (나올 때 Ctrl+B 그리고 D)" ;;
  screen) echo "콘솔 보기: screen -r ${SESSION}        (나올 때 Ctrl+A 그리고 D)" ;;
  nohup)  echo "PID      : $(cat "${PIDFILE}" 2>/dev/null)" ;;
esac
echo "로그     : tail -f ${LOG}"
echo "종료     : bash deploy/stop.sh"
