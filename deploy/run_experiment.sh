#!/usr/bin/env bash
# ============================================================
# 롤링 호라이즌 실험을 서버에서 백그라운드로 돌린다 (SSH 를 끊어도 계속)
#
#   bash deploy/run_experiment.sh 이름 [run_rolling.py 옵션...]
#
# 예)
#   bash deploy/run_experiment.sh set5h_mip --set rolling5h --mip --agv-info 60 --replan event,30 \
#        --workers 16 --threads 8 --mip-time 600 --replan-time 60
#   bash deploy/run_experiment.sh set5h_H   --set rolling5h --agv-info 0,15,30,60,120 --workers 32 --threads 1
#
# 결과: web/experiments/out/이름/ (results.csv · summary.csv · report.md, 도중에는 results_partial.csv)
# 로그: web/experiments/out/이름.log
# 진행: tail -f web/experiments/out/이름.log    멈춤: tmux kill-session -t exp-이름
# ============================================================
set -euo pipefail
NAME="${1:?실험 이름을 주세요 (예: set5h_mip)}"; shift
ENV_NAME="${ENV_NAME:-opt_env}"
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
WEB="${ROOT}/web"
OUT="${WEB}/experiments/out/${NAME}"
LOG="${WEB}/experiments/out/${NAME}.log"
mkdir -p "${WEB}/experiments/out"
if [ -e "${OUT}" ]; then echo "[!] ${OUT} 이 이미 있습니다. 다른 이름을 쓰세요."; exit 1; fi

CONDA_BASE="${CONDA_BASE:-$(conda info --base 2>/dev/null || true)}"
for c in "${CONDA_BASE}" "$HOME/miniconda3" "$HOME/anaconda3" "$HOME/miniforge3"; do
  [ -n "$c" ] && [ -f "$c/etc/profile.d/conda.sh" ] && CONDA_BASE="$c" && break
done
ARGS="$(printf '%q ' "$@")"
CMD="source ${CONDA_BASE}/etc/profile.d/conda.sh && conda activate ${ENV_NAME} && cd ${WEB} && \
     PYTHONUNBUFFERED=1 OMP_NUM_THREADS=1 python experiments/run_rolling.py --out ${OUT} ${ARGS}"

if command -v tmux >/dev/null 2>&1; then
  tmux new-session -d -s "exp-${NAME}" "${CMD} 2>&1 | tee -a ${LOG}"
  echo "[+] tmux 세션 exp-${NAME} 에서 시작했습니다."
else
  nohup bash -c "${CMD}" >> "${LOG}" 2>&1 < /dev/null &
  echo "[+] nohup 으로 시작했습니다 (PID $!)."
fi
echo "    진행: tail -f ${LOG}"
echo "    결과: ${OUT}/report.md (끝난 뒤) · results_partial.csv (도중)"
