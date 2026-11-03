#!/usr/bin/env bash
# ============================================================
# [Mac] 고친 코드를 GitHub 로 올린다
#   bash deploy/push.sh "무엇을 바꿨는지"
# 서버에서는 그다음 bash deploy/update.sh
# ============================================================
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "${ROOT}"
MSG="${1:-update $(date '+%Y-%m-%d %H:%M')}"
if [ ! -d .git ]; then
  echo "[!] 아직 git 저장소가 아닙니다. SERVER.md 1절(최초 1회)을 먼저 하세요."; exit 1
fi
git add -A
if git diff --cached --quiet; then
  echo "[=] 바뀐 파일이 없습니다."; exit 0
fi
echo "바뀐 파일:"; git diff --cached --stat | tail -n 30
git commit -q -m "${MSG}"
# 서버가 올린 결과(results/)가 GitHub 에 먼저 있으면 그 위에 얹어서 올림
git pull --rebase -q || { echo "[!] GitHub 최신판과 합치다 충돌 — git status 확인"; exit 1; }
git push -q
echo "[+] GitHub 에 올렸습니다: $(git log -1 --format='%h %s')"
echo "    서버에서: cd ~/Pre-Determined_Rehandle && bash deploy/update.sh"
