# 연구실 서버에서 돌리기 (Pre-Determined Rehandle)

crane-bench(`Web_crane_scheduling`)를 올렸던 것과 같은 방식입니다.
같은 서버 **10.92.62.51** (가온관 619호, 내부망), 같은 계정 · 같은 conda 환경(`opt_env`) · 같은 Gurobi 라이선스를 씁니다.
둘이 같이 돌 수 있도록 이 프로젝트는 **포트 8766 · tmux 세션 `rehandle`** 을 씁니다 (crane-bench 는 8765 · `cranebench`).

```
 Mac (~/workspace/2026/Pre-Determined_Rehandle)
   │  ① Mac: bash deploy/push.sh "메시지"  →  GitHub (private: hyexnjunee/pre-determined-rehandle)
   │  ② 서버: bash deploy/update.sh (git pull + 웹 재시작)
   ▼
 서버 ~/Pre-Determined_Rehandle
   ├─ web/      웹 (bash deploy/start.sh → http://10.92.62.51:8766)
   │   └─ experiments/out/   실험 결과 (bash deploy/run_experiment.sh …)
   ├─ model/    노트북 (협업 · 비교군 MIP 원본)
   └─ deploy/   서버용 스크립트
```

올라가는 것: `web/`(코드 · 테스트 · 실험 스크립트 · 5시간 실험 세트), `model/`, `docs/`, `deploy/`, `analysis/instance_candidates/`.
**올라가지 않는 것**: 동원 원본 CSV(`data/`), 분석 산출물(`analysis/` 나머지), 웹 실행 결과 · 캐시(`web/data/runs`, `realcache`), PDF.
롤링 실험은 원본 데이터 없이 돌아갑니다 (인스턴스는 생성기 · 실험 세트).

---

## 1. GitHub 연결 (최초 1회) — 이후 코드 업데이트는 전부 git 으로

crane-bench 와 같은 계정(`hyexnjunee`) · 같은 Personal Access Token 을 씁니다.
crane-bench 때 Mac 은 키체인, 서버는 `credential.helper store` 로 토큰을 저장해 두었으니 다시 입력할 일은 거의 없습니다
(토큰이 classic 이고 `repo` 권한이면 새 저장소에도 그대로 됩니다. fine-grained 토큰이면 새 저장소를 권한에 추가 — GIT_GUIDE.md).

**① GitHub** — https://github.com/new → 이름 `pre-determined-rehandle` · **Private** · README 등 아무것도 체크하지 않고 생성

**② Mac**

```bash
cd ~/workspace/2026/Pre-Determined_Rehandle
git init -b main
git add .
git status            # 약 100개 파일. data/ · analysis 큰 CSV · pdf · web/data/runs 결과가 없어야 정상 (.gitignore)
git commit -m "Pre-determined rehandle: 협업 모델 · 롤링 호라이즌 · 5시간 실험 세트 · 서버 배포"
git remote add origin https://github.com/hyexnjunee/pre-determined-rehandle.git
git push -u origin main
```

**③ 서버** — 이미 rsync 로 올린 폴더를 그대로 git 저장소로 바꿉니다 (서버에서 돌린 결과 · 토큰은 그대로 남음)

```bash
cd ~/Pre-Determined_Rehandle
git init -b main
git remote add origin https://github.com/hyexnjunee/pre-determined-rehandle.git
git fetch origin
git reset origin/main                       # 이력 연결 (서버에서 돌린 결과 · 토큰은 git 이 안 건드림)
git checkout -- .                           # 코드 파일을 GitHub 판으로 맞춤 (rsync 때 빠진 .gitignore · push.sh · update.sh 도 생김)
git branch --set-upstream-to=origin/main main
git status                                  # "nothing to commit, working tree clean" 이면 끝
```

> 처음부터 새로 받는 경우는 `git clone https://github.com/hyexnjunee/pre-determined-rehandle.git ~/Pre-Determined_Rehandle`.

### 이후 코드 업데이트 (매번)

```bash
# Mac
cd ~/workspace/2026/Pre-Determined_Rehandle && bash deploy/push.sh "무엇을 바꿨는지"
# 서버
cd ~/Pre-Determined_Rehandle && bash deploy/update.sh      # git pull + 웹이 켜져 있으면 다시 띄움
```

`update.sh` 는 돌고 있는 실험(tmux `exp-*`)은 건드리지 않습니다 (실험은 시작할 때의 코드로 끝까지 감).
서버에서는 코드를 직접 고치지 마세요 — 고칠 일은 Mac 에서 고쳐 push 하면 충돌이 생기지 않습니다.

### (참고) git 없이 rsync 로 올리기 — 처음 올릴 때 쓴 방법

```bash
cd ~/workspace/2026/Pre-Determined_Rehandle
rsync -avR --delete --exclude '__pycache__' --exclude '.DS_Store' --exclude '.ipynb_checkpoints' \
  --exclude 'web/data/runs/' --exclude 'web/data/realcache/' --exclude 'web/data/instances/' \
  --exclude 'web/data/.access_token' --exclude 'web/data/server.*' --exclude 'web/experiments/out/' \
  --exclude 'analysis/instance_candidates/out/' --exclude '*.pdf' \
  web model docs deploy SERVER.md README.md analysis/instance_candidates \
  hyeonjun@10.92.62.51:~/Pre-Determined_Rehandle/
```

git 으로 바꾼 뒤에는 쓰지 마세요 (서버의 `.git` 과 어긋날 수 있음).

---

## 2. 서버 환경 (crane-bench 를 이미 세팅했다면 거의 할 일 없음)

```bash
ssh hyeonjun@10.92.62.51            # 또는 ssh lab
cd ~/Pre-Determined_Rehandle
bash deploy/setup_env.sh            # opt_env 재사용 + matplotlib · gurobipy 확인 → 자동으로 진단
```

진단(`python deploy/check_env.py`)이 확인하는 것:
1. 파이썬 · 코어 수  2. matplotlib  3. **Gurobi 크기 제한 없는 라이선스인지** (6000 변수 모델을 실제로 풀어 봄)
4. 노트북 · 방법 7개 · 5시간 실험 세트 43개 · 롤링 한 번 실행

> 5시간 인스턴스(작업 52~109개, rehandle ~90개)의 협업 MIP 는 **크기 제한 라이선스로는 안 풀립니다.**
> crane-bench 때 받은 `~/gurobi.lic` 가 같은 계정에 있으면 그대로 쓰입니다. 라이선스 종류는 crane-bench `SERVER.md` 3절.

---

## 3. 웹 띄우기

```bash
bash deploy/start.sh                 # 내부망 공개 + 토큰, tmux 세션 'rehandle' (SSH 끊어도 유지)
bash deploy/status.sh                # 실행 여부 · 접속 주소 · 돌고 있는 실험
bash deploy/stop.sh                  # 종료
SHARE=0 bash deploy/start.sh         # 나만 (VS Code PORTS 탭으로 열기)
bash deploy/install_autostart.sh     # 재부팅 후 자동 시작 (해제: --remove)
```

접속: `http://10.92.62.51:8766/?token=…` (주소는 `status.sh` 가 보여줌). 학교 밖에서는 VPN 필요.
웹의 MIP 자원 상한은 환경 변수로 (기본 16 스레드):

```bash
CRANEBENCH_DEFAULT_THREADS=16 CRANEBENCH_MAX_THREADS=32 CRANEBENCH_MAX_TIME_LIMIT=7200 bash deploy/start.sh
```

---

## 4. 실험 돌리기 (백그라운드, SSH 끊어도 계속)

```bash
bash deploy/run_experiment.sh 이름 [run_rolling.py 옵션…]
tail -f web/experiments/out/이름.log          # 진행
tmux kill-session -t exp-이름                  # 멈춤 (끝난 인스턴스까지는 results_partial.csv 에 남음)
```

`--workers N` 이면 인스턴스 N 개를 동시에, MIP 는 각각 `--threads T` 스레드 → **총 코어 ≈ N × T**.
서버는 128코어 · 256스레드지만 랩실 공용이니 평소 **64 안팎**으로 쓰세요 (`htop` 으로 다른 사람 사용량 확인).

추천 실험 (5시간 세트 43개):

```bash
# ① 휴리스틱: AGV 정보 시간 H × 작업 투입 방식 — 빠름 (수십 분)
bash deploy/run_experiment.sh set5h_heur --set rolling5h --agv-info 0,15,30,60,120 --replan event,15,30,60 \
     --workers 32 --threads 1

# ② MIP 상한 · 롤링 협업 MIP — 오래 걸림 (정적 MIP 인스턴스당 최대 30분)
bash deploy/run_experiment.sh set5h_mip --set rolling5h --mip --agv-info 60 --replan event,30 \
     --workers 8 --threads 8 --mip-time 1800 --replan-time 60

# ③ 극단 세트만 먼저 빠르게 확인
bash deploy/run_experiment.sh ext_mip --set rolling5h --set-group 극단 --mip --agv-info 60 --replan 30 \
     --workers 8 --threads 8 --mip-time 600 --replan-time 30
```

결과 폴더 `web/experiments/out/이름/`: `report.md`(표) · `summary.csv`(조건별 평균) · `results.csv`(인스턴스 × 방법) · `config.json` · `instances/`.

---

## 5. 결과 가져오기 (Mac 에서)

```bash
cd ~/workspace/2026/Pre-Determined_Rehandle/web/experiments/out
rsync -av hyeonjun@10.92.62.51:~/Pre-Determined_Rehandle/web/experiments/out/ ./
# 웹에서 돌린 실행(결과 보관함)도 가져오려면
rsync -av hyeonjun@10.92.62.51:~/Pre-Determined_Rehandle/web/data/runs/ ../../data/runs/
```

---

## 6. (선택) 실데이터 가져오기 기능까지 서버에서 쓰려면

웹의 "실데이터 가져오기" 는 동원 원본 CSV(`data/`, 약 300 MB)가 필요합니다. **외부 반출이 허용된 범위인지 먼저 확인**한 뒤:

```bash
rsync -av ~/workspace/2026/Pre-Determined_Rehandle/data/ hyeonjun@10.92.62.51:~/Pre-Determined_Rehandle/data/
```

`deploy/start.sh` 는 `~/Pre-Determined_Rehandle/data` 가 있으면 자동으로 씁니다. 롤링 실험에는 필요 없습니다.

---

## 7. 코드를 고쳤을 때

1절 "이후 코드 업데이트": Mac `bash deploy/push.sh "메시지"` → 서버 `bash deploy/update.sh`.
