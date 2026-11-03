#!/usr/bin/env bash
# ============================================================
# [서버] 실험 결과를 GitHub 로 올린다 → Mac 에서 bash deploy/pull.sh
#   bash deploy/push_results.sh              # 끝난 결과 전부
#   bash deploy/push_results.sh "새 정보마다"   # 이름에 이 글자가 든 것만
#                                              (웹 실행: 실행 이름 · 인스턴스 이름, 실험 폴더: 폴더 이름)
#
# 올리는 것 (저장소의 results/ 폴더, 코드는 건드리지 않음)
#   results/runs/<run_id>/run.json      웹 결과 보관함 (끝난 실행만, 그림 PNG · 로그는 뺌)
#   results/experiments/<이름>/          run_experiment.sh 결과 (report.md · summary.csv · results.csv · config.json, instances/ 는 뺌)
#   results/INDEX.md                    웹 실행 목록 (방법별 트럭 대기 합)
# 실데이터(동원 원본에서 만든 인스턴스) 실행은 기본으로 뺀다.
#   외부 반출이 허용된 것을 확인했으면: WITH_REAL=1 bash deploy/push_results.sh
# ============================================================
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "${ROOT}"
FILTER="${1:-}"
WITH_REAL="${WITH_REAL:-0}"
if [ ! -d .git ]; then
  echo "[!] git 저장소가 아닙니다 (SERVER.md 1절)."; exit 1
fi

python3 - "${FILTER}" "${WITH_REAL}" <<'PY'
import glob, json, os, shutil, sys, time

flt, with_real = sys.argv[1], sys.argv[2] == "1"
OUT = "results"
os.makedirs(f"{OUT}/runs", exist_ok=True)
os.makedirs(f"{OUT}/experiments", exist_ok=True)

def same(a, b):
    return os.path.exists(b) and os.path.getsize(a) == os.path.getsize(b) and open(a, "rb").read() == open(b, "rb").read()

new = skip_real = skip_run = 0
for p in sorted(glob.glob("web/data/runs/*/run.json")):
    rid = os.path.basename(os.path.dirname(p))
    try:
        d = json.load(open(p, encoding="utf-8"))
    except Exception:
        continue
    if not d.get("finished_at") or d.get("cancelled"):
        skip_run += 1; continue
    meta = (d.get("instance") or {}).get("meta") or {}
    if meta.get("source") == "realdata" and not with_real:
        skip_real += 1; continue
    name = f'{d.get("label") or ""} {d.get("instance_name") or ""}'
    if flt and flt not in name:
        continue
    dst = f"{OUT}/runs/{rid}/run.json"
    if not same(p, dst):
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        shutil.copy2(p, dst); new += 1
        print(f"  + 웹 실행 {rid}  {d.get('label')}  ({d.get('instance_name')})")

KEEP = {"report.md", "summary.csv", "results.csv", "results_partial.csv", "config.json"}
for d in sorted(glob.glob("web/experiments/out/*/")):
    name = os.path.basename(d.rstrip("/"))
    if flt and flt not in name:
        continue
    files = [f for f in os.listdir(d) if f in KEEP]
    if not files:
        continue
    for f in files:
        src, dst = os.path.join(d, f), f"{OUT}/experiments/{name}/{f}"
        if not same(src, dst):
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            shutil.copy2(src, dst); new += 1
            print(f"  + 실험 {name}/{f}")

# 목록: 올려 둔 웹 실행 전부 (방법별 트럭 대기 합)
COLS = [("baseline", "비교군"), ("coop", "협업"), ("rolling_baseline", "롤링 비교군"), ("rolling_coop", "롤링 협업")]
rows = []
for p in sorted(glob.glob(f"{OUT}/runs/*/run.json")):
    try:
        d = json.load(open(p, encoding="utf-8"))
    except Exception:
        continue
    res = {r.get("method"): r for r in (d.get("results") or []) if isinstance(r, dict)}
    def cell(m):
        r = res.get(m)
        if not r: return ""
        if r.get("objective") is None: return r.get("status") or "오류"
        s = f'{r["objective"]:,.0f}'
        g = r.get("mip_gap")
        if g: s += f' (gap {g*100:.1f}%)'
        return s
    t = time.strftime("%m-%d %H:%M", time.localtime(d.get("created_at") or 0))
    other = [m for m in res if m not in dict(COLS)]
    rows.append(f'| {t} | `{os.path.basename(os.path.dirname(p))}` | {d.get("label") or ""} | {d.get("instance_name") or ""} | '
                f'{d.get("num_jobs")}/{d.get("num_rehandles")} | ' + " | ".join(cell(m) for m, _ in COLS) +
                f' | {", ".join(other)} |')
with open(f"{OUT}/INDEX.md", "w", encoding="utf-8") as f:
    f.write("# 서버 실험 결과 (deploy/push_results.sh 가 만든 목록)\n\n"
            "트럭 대기 합(초). 협업 = AGV 60분 전 + 모든 도착을 미리 아는 정적 MIP, 롤링 = 새로 알게 될 때마다 재계획.\n\n"
            "| 시작 | 실행 | 이름 | 인스턴스 | 작업/RH | " + " | ".join(n for _, n in COLS) + " | 그 밖 |\n"
            "|---|---|---|---|---|" + "---|" * len(COLS) + "---|\n" + "\n".join(rows) + "\n")
print(f"[=] 새로 · 바뀐 파일 {new}개  (실데이터 실행 제외 {skip_real}개, 안 끝난 · 취소 {skip_run}개)")
PY

git add results
if git diff --cached --quiet -- results; then
  echo "[=] GitHub 에 올릴 새 결과가 없습니다."; exit 0
fi
git diff --cached --stat -- results | tail -n 15
WHO=()
[ -z "$(git config user.email || true)" ] && WHO=(-c "user.name=$(whoami) (lab server)" -c "user.email=$(whoami)@$(hostname)")
git ${WHO[@]+"${WHO[@]}"} commit -q -m "서버 실험 결과 $(date '+%Y-%m-%d %H:%M')${FILTER:+ · ${FILTER}}" -- results
git pull --rebase --autostash -q || { echo "[!] GitHub 최신판과 합치지 못했습니다. git status 를 확인하세요."; exit 1; }
if ! git push -q; then
  echo "[!] push 실패. 토큰에 쓰기 권한이 없으면 SERVER.md 5절 (rsync 로 가져오기) 을 쓰세요."; exit 1
fi
echo "[+] GitHub 에 올렸습니다: $(git log -1 --format='%h %s')"
echo "    Mac 에서: cd ~/workspace/2026/Pre-Determined_Rehandle && bash deploy/pull.sh"
