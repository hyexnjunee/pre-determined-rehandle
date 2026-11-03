#!/usr/bin/env bash
# ============================================================
# [Mac] 서버가 GitHub 에 올린 실험 결과(results/)를 받는다
#   bash deploy/pull.sh
# 서버에서 먼저: bash deploy/push_results.sh
# ============================================================
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "${ROOT}"
before="$(git rev-parse --short HEAD)"
git pull --rebase --autostash -q
after="$(git rev-parse --short HEAD)"
if [ "${before}" = "${after}" ]; then
  echo "[=] 이미 최신입니다 (${after})."; exit 0
fi
echo "[+] ${before} → ${after}"; git log --oneline "${before}..${after}" | head -n 10
echo "받은 결과:"; git diff --stat "${before}" "${after}" -- results | tail -n 20
echo "    목록: results/INDEX.md"
