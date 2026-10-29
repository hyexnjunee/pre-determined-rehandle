#!/usr/bin/env bash
# ============================================================
# 연구실 서버 초기 세팅 (sudo 불필요, 본인 홈에만 설치)
#   bash deploy/setup_env.sh
# crane-bench 에서 쓰던 opt_env 를 그대로 쓰고, 이 프로젝트에 필요한 것만 더한다.
# (웹 서버 자체는 표준 라이브러리만 씀. Gurobi 라이선스는 SERVER.md 3절)
# ============================================================
set -euo pipefail
ENV_NAME="${ENV_NAME:-opt_env}"
PY_VER="${PY_VER:-3.11}"
if ! command -v conda >/dev/null 2>&1; then
  echo "[!] conda 가 없습니다. Miniconda 를 먼저 설치하세요 (crane-bench SERVER.md 2절과 같음)."; exit 1
fi
eval "$(conda shell.bash hook)"
if conda env list | awk '{print $1}' | grep -qx "${ENV_NAME}"; then
  echo "[=] 기존 환경 ${ENV_NAME} 을 사용합니다."
else
  echo "[+] 환경 생성: ${ENV_NAME}"
  conda create -n "${ENV_NAME}" "python=${PY_VER}" -y -c conda-forge
fi
conda activate "${ENV_NAME}"
python -m pip install --upgrade pip
# matplotlib: 노트북 원본 차트 / gurobipy: 협업·비교군 MIP / numpy·pandas: 분석
python -m pip install matplotlib gurobipy numpy pandas
echo; echo "[=] 설치 완료. 진단을 돌립니다."; echo
python "$(dirname "$0")/check_env.py" || true
