# Rehandle 협업 스케줄링 Web

Predetermined sequence twin crane 블록에서 **비교군**(rehandle 을 원래 크레인이 본 작업 처리시간 안에서 수행)과
**협업 모델**(rehandle 을 독립 작업으로 두고 반대편 크레인이 대신할 수 있음)을 **같은 인스턴스**로 돌리고,
결과를 웹에서 비교하고, 실행 기록을 폴더에 쌓아 두는 실험 시스템.

예전 `Web_crane_scheduling` 프로젝트와 같은 구조다. 모델 원본은 계속 노트북이고, 웹은 실행할 때마다
노트북을 읽어 데이터·파라미터 셀만 바꿔 넣고 그대로 실행한다.

```
Pre-Determined_Rehandle/
  model/
    Reha_Crane_baseline.ipynb        ← 비교군 (key: baseline)
    Reha_Crane_predetermined.ipynb   ← 협업 모델 (key: coop)
  web/                               ← 이 폴더
```

## 1. 실행

Gurobi 가 설치된 파이썬(= 노트북을 돌리던 환경)으로 실행한다. 서버는 표준 라이브러리만 쓴다.

```bash
cd Pre-Determined_Rehandle/web
python run_server.py                 # http://127.0.0.1:8766 자동으로 열림
python run_server.py --port 9000 --no-browser
python run_server.py --share         # 내부망 공개 + 토큰 인증 (연구실 서버용)
python run_server.py --notebooks /다른/노트북/폴더
```

시작할 때 두 노트북의 연결 상태가 찍힌다.

```
OK  baseline   비교군 (Rehandle 처리시간 포함)  <- Reha_Crane_baseline.ipynb
OK  coop       협업 모델 (Rehandle 할당)  <- Reha_Crane_predetermined.ipynb
```

점검 (노트북을 고친 뒤 먼저 돌려 볼 것):

```bash
python tests/test_pipeline.py
```

두 노트북이 풀리는지, 협업 모델에서 지원을 끄면 비교군과 같은 값이 나오는지,
HTTP 실행·저장·논문 표가 동작하는지까지 확인한다.

## 2. 화면

| 탭 | 내용 |
|---|---|
| **홈** | 비교할 모델 선택, 시작할 인스턴스 선택, 노트북 연결 상태 |
| **실행** | 인스턴스 편집기(파라미터 · SC 순서 · LC 순서 · 작업 위치 미리보기), 무작위 생성, 목적함수(두 모델 공통), 모델별 옵션, 실행 콘솔, 비교표 + **비교군 대비 개선율**, 공통 Gantt, 노트북 원본 차트, 상세 스케줄·로그 |
| **결과 보관함** | 저장된 실행 목록 → 그때의 비교표·개선율·Gantt·차트·로그를 다시 봄. 전체 CSV / 스케줄 CSV |
| **논문 표** | 고른 실행들을 `인스턴스 × 모델` 표로. 지표: makespan, GO 대기 합, 개선율(%), 지원 RH, Gap, 시간, 상태. CSV / Markdown / LaTeX(booktabs) |
| **인스턴스** | 저장된 인스턴스 목록 · 구성 · 그 인스턴스로 돌린 실행 기록 |

### 비교 지표

| 지표 | 정의 |
|---|---|
| makespan | 마지막 작업 완료 시각 |
| Σ 트럭 대기 | 모든 작업의 트럭 (DS·LD 내부 트럭, GI·GO 외부 트럭) 에 대해 Σ (트럭 출발 − 트럭 도착 a). DS·GI 는 크레인이 TP 에서 집는 순간(시작 + pick), LD·GO 는 내려놓는 순간(완료)에 트럭 출발 |
| 내부 / 외부 트럭 대기 | DS·LD 합 / GI·GO 합 |
| GO 대기 | GO 트럭만 |
| 지원 RH | 반대편 크레인이 대신한 rehandle 수 / 전체 rehandle 수 |
| 개선율 | (비교군 − 협업) / 비교군 × 100, 같은 실행 안에서 계산 |
| 작업 시간 합 | 크레인별 처리시간 합 (rehandle 을 넘겨받은 쪽은 늘고 넘긴 쪽은 줄어든다) |

목적함수는 실행마다 하나만 고르고 두 모델에 같이 넣는다 (`truck_wait` 기본 / `makespan` / `go_wait`).
트럭 대기 목적함수에서 도착 시각 a 는 상수라 최적해를 바꾸지 않고, 스케줄에는 제약 a ≤ s 로만 영향을 준다.
bound · gap · `*`(최적 증명)는 그 목적함수 기준이다.

### Gantt 읽는 법

- 두 모델을 **같은 시간 축**으로 위아래에 쌓아 보여준다.
- 빗금 = rehandle. 비교군은 본 작업 막대 앞부분에 빗금(처리시간 안에 포함된 rehandle),
  협업 모델은 독립 막대. 보라색 = 반대편 크레인이 대신한 rehandle.
- ▲ = 트럭 도착 시각 (작업 유형 색).

## 3. 인스턴스 형식

```jsonc
{
  "id": "toy_12", "name": "Toy 12작업",
  "block":  {"n_bay": 30},                    // 해측 TP 0, 야드 1~30, 육측 TP 31
  "params": {"t_hat": 1, "pick": 1, "drop": 1, "safety_margin": 1,
             "rh_inplace": 3, "default_ready": 0},
  "seq_sc": [{"id": "DS_1", "type": "DS", "bay": 20},
             {"id": "LD_2", "type": "LD", "bay": 15, "r_c": 2, "rh_d": [15, 13]}],
  "seq_lc": [{"id": "GO_1", "type": "GO", "bay": 14, "r_c": 3, "a": 0}]
}
```

- `seq_sc`, `seq_lc` 의 순서가 곧 predetermined sequence. SC 는 DS/LD, LC 는 GI/GO 만.
- `r_c` 는 LD·GO 에만. `rh_d` 는 rehandle 별 내려놓을 bay (없으면 같은 bay 안에서 옮김, 처리시간 ρ).
- `a` 는 그 작업 트럭의 도착 시각 (모든 작업). 비우면 `default_ready`.

### 무작위 생성

DS·LD·GI·GO 개수, rehandle 발생 확률과 최대 개수, 다른 bay 로 옮길 확률과 거리 상한,
트럭 도착 간격(SC 내부 / LC 외부, 크레인 순서를 따라 증가), 시드를 정해 여러 개를 한 번에 만든다. 시드를 주면 같은 인스턴스가 다시 나온다.

## 4. 노트북 주입 지점

웹은 노트북 코드를 복사하지 않는다. 실행할 때마다 아래 지점만 바꾼다.

| 셀 | 찾는 표식 | 바꾸는 것 |
|---|---|---|
| 데이터 | `Seq_SC = {` | `N_BAY`, `Seq_SC`, `Seq_LC` |
| 파라미터 | `rh_inplace =` | `safety_margin` `t_hat` `pick` `drop` `rh_inplace` `OBJ` (+ 협업 모델 `ALLOW_ASSIST` `HOME_RH_FIXED`) |
| 데이터 (크레인) | `Crane = {` | 인스턴스에 시작 위치(`sc_init`, `lc_init`)가 있을 때만 `initial` 값을 바꿈 |
| 솔브 | `model.Params.TimeLimit` | TimeLimit, OutputFlag, MIPGap, Threads, LogFile, 취소 콜백 (`model.optimize()` 앞에 삽입) |
| 시각화 | `crane_colors = {` | 그대로 실행하되 soft 셀 (실패해도 해는 유지) |

해 추출은 노트북 네임스페이스의 `model`, `s`, `duration`, `Tasks`, `makespan` 과
협업 모델의 `Set_M`, `Set_R`, `home`, `parent`, `cand`, `x`, 비교군의 `Set_T`, `home`, `rehandle_time` 을 읽는다.
**이 이름들만 바꾸지 않으면** 노트북을 고쳐도 웹이 그대로 따라간다.
바꿨다면 `app/methods/notebook_model.py` 의 `build_cells()` 와 각 어댑터의 `extract()` 만 고치면 된다.

## 5. 모델 추가

```python
# app/methods/my_model.py
from .notebook_model import COMMON_OPTIONS, NotebookModel

class MyModel(NotebookModel):
    key, label, short, role = "my_model", "내 모델", "내 모델", "proposed"
    notebook = "My_Model.ipynb"            # model/ 폴더 안
    options = dict(COMMON_OPTIONS)

    def extract(self, res, ns, inst, options):
        ...  # res.schedule.append(ScheduleEntry(...))
```

`app/registry.py` 의 `build_registry()` 목록에 한 줄 추가하면 홈 카드·비교표·Gantt·논문 표가 자동으로 붙는다.
비교군(`baseline`)을 맨 앞에 두어야 개선율이 비교군 기준으로 계산된다.

### 휴리스틱 (`app/methods/window_rule.py`)

| key | 내용 | MIP 대비 기준 |
|---|---|---|
| `window_rule` | Window–Slack 지원 규칙 (`app/methods/window_rule.py`): 원래 크레인이 바쁠 때 위 rehandle 부터 하나씩 반대편이 넘겨받고, 지원 크레인 자기 트럭이 오면 멈춤. 판단 기록·멈춘 이유를 결과 메모에 남김 | `coop` |
| `window_rule_v2` | ver2 = ver1 + ① 빈틈 탐색(첫 자리에서 지원 크레인 트럭에 걸리면 지원 크레인 자기 작업 사이의 다음 빈틈들을 차례로 시도) + ② 사후 재검사(새로 넣은 지원 때문에 앞서 넣은 지원이 자기 트럭 도착 뒤로 밀리면 그 자리는 쓰지 않음). 둘 다 끄면 ver1 과 같은 해 | `coop` |

`coop`·`window_rule`·`window_rule_v2` 의 "rehandle 은 트럭 도착 뒤에만 시작" (`RH_AFTER_ARRIVAL`) 은 기본 켬이다.
켠 상태가 다르면 "MIP 대비" 칸에 "조건 다름" 으로 표시하고 값을 계산하지 않는다.
같은 세 모델에 "적하(LD) rehandle 은 AGV 도착 H분 전부터 미리 가능" (`LD_LOOKAHEAD`, 기본 끔 · H 30분) 옵션이 있다.
반출은 외부트럭 도착을 모른다고 보고 트럭 도착 뒤에만 한다. 실험용 인스턴스는 생성기 "적하 rehandle 실험값" (실데이터는 LD rehandle 3.5 % 로 드묾),
실험 스크립트는 `--preset ld --ld-lookahead 30`.
공통 Gantt 에서 빨간 테두리 = 지원 크레인의 다음 자기 트럭이 이미 와 있는데 시작한 지원 rehandle (제목 줄에 개수).
비교표의 "겹침 / 대기" 는 지원 rehandle 이 원래 크레인의 다른 작업과 동시에 진행된 시간 / 원래 크레인이 rehandle 끝나기를 기다린 시간이다.
생성기의 "대기열 있는 보정값" 은 실데이터 보정값에서 LC 트럭 간격만 200 s 로 줄인 것 (반출 트럭이 줄 서 있는 상황).
생성기 "도착 방식" 은 일정 간격 / 무작위(포아송, 간격이 지수분포) / 몰려서 도착(몇 대씩 한꺼번에), "GO bay 위치" 는 균등 / 바다 쪽 / 육지 쪽.
평균 간격은 그대로이고, 기본값(일정 간격 · 균등)이면 같은 seed 에서 예전과 똑같은 인스턴스가 나온다.

솔버 없이 노트북과 같은 제약(ETD 포함)으로 스케줄을 만들고 검증한다. 같은 실행에서 기준 MIP 가 먼저 풀리면
`app/comparison.py` 가 비교표 "MIP 대비" 칸을 채운다: 기준이 최적이면 RPD(%), 아니면 incumbent 대비 편차(%), 그리고 속도 배수(×).
휴리스틱 결과도 "노트북 원본 차트" 카드에 나온다: `app/nbchart.py` 가 MIP 노트북의 시각화 셀을 그대로 가져와
휴리스틱 스케줄을 넣어 그린다 (rehandle 을 분리한 결과는 협업 노트북 셀, rehandle 을 본 작업에 합친 결과는 비교군 노트북 셀).
카드의 "나란히 비교 (2개)" 로 두 모델 차트를 옆에(또는 위아래로) 놓고 비교할 수 있다.
크레인 시작 위치(`sc_init`, `lc_init`)도 노트북과 똑같이 반영한다. 점검: `python tests/test_rules.py` (`--mip` 이면 노트북 모델에 고정해 확인).

### 비교 실험 일괄 실행 (`experiments/run_experiments.py`)

```bash
python experiments/run_experiments.py --groups small --seeds 20                     # 소형 검증 (MIP 최적 → RPD)
python experiments/run_experiments.py --groups grid --seeds 30 \
       --sc-gaps 584,300 --lc-gaps 739,400,200 --arrivals even,poisson,burst          # 부하 격자 (휴리스틱만, --mip 이면 MIP 도)
python experiments/run_experiments.py --groups real                                   # 실데이터 후보 구간
```

결과는 `experiments/out/<시각>/` 에 `results.csv`(인스턴스 × 방법), `summary.csv`(조건별 평균), `report.md`(표), `instances/`(재현용 JSON).
지표: 무지원 대비 개선(창 규칙에서 지원만 끈 것 대비), 비교군 MIP 대비 개선, 협업 MIP 대비 RPD(협업 MIP 가 OPTIMAL 일 때), 지원 RH 수, 겹침 / 기다림.
실데이터 후보 구간은 `../analysis/instance_candidates/candidates.csv` (고른 기준은 그 폴더 README).
웹에서는 실행 탭 → "실데이터 가져오기" 맨 위 **추천 실험 구간** 표에서 행을 누르면 그 구간이 바로 불러와지고,
"목록에 저장" 을 누르면 보이는 구간을 `[추천 A1] …` 이름으로 인스턴스 목록에 한꺼번에 저장한다.

### 롤링 호라이즌 실험 (`롤링 · …` 방법, `experiments/run_rolling.py`, 2026-10-28)

정보가 시간에 따라 들어오는 상황: 외부트럭(GI·GO)은 **도착해야** 알고(반출 rehandle 도 도착 뒤에만),
AGV(DS·LD)는 **도착 H분 전**(기본 60분)에 안다(적하 rehandle 은 그때부터 미리 가능).
새 정보가 들어올 때마다 "크레인별로 앞에서부터 공개된 작업까지"만 넣어 다시 계획하고(`app/methods/rolling.py`),
다음 정보 전에 시작하는 작업만 확정한다. 확정된 작업은 계획 시각보다 앞당기지 않고, 상대 크레인의 진행 중 작업과
ETD 가 맞지 않으면 그만큼 늦춰 실행 → 최종 스케줄은 항상 노트북 제약을 지킨다 (끝에서 다시 검사, `--mip` 테스트로 노트북에 고정 확인).

| 웹 방법 | 안쪽 계획 방법 |
|---|---|
| 롤링 · Window–Slack ver2 | 창 규칙 v2 (AGV H분 전 · 반출 도착 뒤 규칙) |
| 롤링 · 지원 없음 | 같은 설정에서 지원만 끔 (기준) |
| 롤링 · 협업 MIP | 협업 MIP, 재계획 1회 제한 시간 옵션 |

옵션: AGV 정보 시간 H(분), 외부트럭 정보 시간(분, 기본 0 = 도착해야 앎),
**작업 투입(재계획) 방식** — `새 정보마다`(정보가 들어오는 순간마다) 또는 `일정 간격`(첫 작업 시각부터 Δ분마다, 기본 30분:
그 사이 정보가 들어온 작업은 다음 투입 때 한꺼번에 계획에 들어가고, 다음 투입 전에 시작하는 작업만 확정).
"앞으로 30분치 작업을 30분마다 내려준다" 는 `일정 간격 30분` + AGV 정보 30분 + 외부트럭 정보 30분으로 표현한다 (반출 rehandle 은 그래도 트럭 도착 뒤).
공통 Gantt 에 투입 시점이 보라 점선으로, 그때 새로 들어온 작업 수가 `+n` 으로 표시된다.
**시간 관리** (큰 · 극단 인스턴스에서 끝나지 않을 때): `재계획 1회 제한 (s)` 은 MIP · 규칙 한 번의 제한,
`롤링 전체 제한 (s, 기본 900)` 은 롤링 전체 — 남은 시간을 남은 투입 수로 나눠 1회 제한을 줄이고, 다 쓰면 남은 재계획은
창 규칙 v2 1초(안쪽이 창 규칙이면 1초 제한)로 마무리한다. `크레인별 앞에서 몇 개까지 계획 (기본 12)` 은 대기열이 길 때
재계획 문제 크기를 묶는다 (잘렸으면 확정이 나오는 동안 같은 시점에서 다음 묶음을 이어서 계획). 진행은 로그 탭에 `[롤링] 투입 k/N …`.
AGV 사전 정보로 **AGV 도착 전에 시작한 적하 rehandle** 은 초록 테두리(도착 뒤까지 이어지면 점선)로, LC·SC 행 사이 **"AGV 정보" 띠** 에
적하 작업마다 정보 공개(◆, 도착 H분 전) → AGV 도착(▼) 선과, 미리 한 rehandle → 도착 → 해측 적하 시작 점선이 그려진다.
`사전 수행만 강조` 버튼을 누르면 그것들만 진하게 보인다.
결과 요약 카드에는 모델 이름이 붙고, 롤링 결과는 보라색 카드 한 줄이 더 나온다: 정보·투입 설정, AGV 도착 전에 시작한 적하 rehandle,
외부트럭 투입 대기(트럭이 와도 다음 투입까지 계획에 못 들어간 시간), 롤링 · 지원 없음 대비(지원 효과), 정적 결과 대비(정보 손실 —
같은 실행에 정적 협업 모델 · 창 규칙 v2 가 있을 때. AGV 사전 정보 조건이 다르거나 정적 MIP gap 이 크면 경고), 재계획 · 실행 보정.
생성기의 **롤링 실험값** = 적하 rehandle 실험값 × 작업 2배(DS 8 · LD 12 · GI 8 · GO 8, 약 2~3시간).
같은 조건의 상한은 "협업 모델" 에 `AGV 도착 H분 전부터` 옵션을 켠 정적 해(모든 도착을 처음부터 앎).

```bash
python experiments/run_rolling.py --seeds 20 --agv-info 0,15,30,60,120               # H 를 바꿔 가며 (휴리스틱만)
python experiments/run_rolling.py --seeds 20 --sc-gaps 584,400 --lc-gaps 739,400      # 부하
python experiments/run_rolling.py --jobs "" --sc-gaps 800 --lc-gaps 900 --mip         # 작은 인스턴스 + 롤링 협업 MIP · 정적 MIP
```

**실험 세트 (5시간)** — 실행 탭 인스턴스 카드의 `실험 세트 (5시간)` 버튼. 트럭 도착이 5시간에 고르게 퍼진 인스턴스 43개:
균형 27개(SC·LC 부하 0.5 / 0.7 / 0.9 격자 × 시드 3)와 극단 16개(SC 과부하·LC 한가, LC 과부하·SC 한가, 둘 다 과부하, 둘 다 한가,
적하 rehandle 폭증, 반출 rehandle 폭증, 트럭 몰림, 반출 해측 몰림 × 시드 2). 부하 지도(점)와 표에서 누르면 편집기에 불러오고,
`목록에 저장` 으로 인스턴스 목록에 `[5h 균형] …`, `[5h 극단] …` 이름으로 저장한다. 정의는 `app/instance_sets.py`(시드 고정),
파일은 `data/instance_sets/rolling5h/` (`python experiments/make_instance_sets.py` 로 다시 만듦, 없으면 서버가 정의로 바로 만듦).

**10시간 세트** (`rolling10h`, 같은 패널의 위 탭): 균형 18개(부하 0.5/0.7/0.9 격자 × 시드 2), 대규모 6개(작업 210~260개:
부하 1.0 · 과부하 1.15 · rehandle 이 드물어 작업이 짧고 아주 많은 1.0), 몰림 20개(평균 부하는 SC·LC 0.7 로 같고
도착이 시간대에 몰림 — 오전 · 한낮 · 저녁 몰림, 두 번 몰림, 1시간 폭주, SC만 · LC만 몰림, 엇갈린 몰림, 동시 몰림, 점점 증가 × 시드 2).
표의 "가장 바쁜 1시간" = 1시간 창(15분씩 밀며)에 도착한 작업량 ÷ 1시간의 최댓값, "시간별 도착" = 시간마다 도착 수(파랑 SC · 주황 LC).
부하 지도는 "평균 부하 / 가장 바쁜 1시간" 으로 바꿔 볼 수 있다.

```bash
python experiments/run_rolling.py --set rolling5h --agv-info 60                 # 세트 전체
python experiments/run_rolling.py --set rolling5h --set-group 극단 --agv-info 0,60
python experiments/run_rolling.py --set rolling10h --set-group 몰림 --agv-info 60 --workers 8
python experiments/run_rolling.py --set rolling5h --agv-info 60 --replan event,15,30,60  # 작업 투입 간격 비교
python experiments/run_rolling.py --set rolling5h --mip --workers 8 --threads 8          # 인스턴스 8개 동시 (서버)
```

연구실 서버(10.92.62.51)에서 웹 · 실험을 돌리는 방법은 저장소 맨 위 `SERVER.md`
(`deploy/start.sh` 웹, `deploy/run_experiment.sh` 백그라운드 실험, `deploy/check_env.py` 진단).
`--workers` 로 돌리면 끝난 인스턴스까지 `results_partial.csv` 에 계속 저장된다.

결과 표: 무지원 대비 %(롤링 · 지원 없음 대비), 정보 손실 %(같은 방법의 정적 해 대비), 적하 / 반출 지원 수,
AGV 도착 전에 끝낸 적하 rehandle 수, AGV 대기 · 외부트럭 대기, 재계획 횟수.

## 6. 저장 위치

```
web/data/instances/<id>.json
web/data/runs/<run_id>/run.json, instance.json, log_<model>.txt, fig_<model>_<n>.png
```

실행할 때 `결과 저장` / `인스턴스 저장` 체크박스로 고른다. 둘 다 끄면 파일이 남지 않는다.

## 7. 구조

```
web/
  run_server.py
  app/
    schema.py              인스턴스 형식 + 검증 + 기본 인스턴스
    generator.py           무작위 인스턴스 생성
    nbrunner.py            노트북 실행 엔진 (예전 프로젝트와 동일)
    livelog.py             실시간 로그 (동일)
    limits.py              스레드·메모리·시간 상한
    registry.py            모델 등록 (노트북 폴더 = ../model)
    storage.py             인스턴스·결과 저장, 논문 표 집계, CSV
    server.py              HTTP API
    static/index.html      웹 UI 전체 (단일 파일)
    methods/
      base.py              결과 형식 + 지표(GO 대기, 지원 RH, 작업 시간)
      notebook_model.py    노트북 주입·실행 공통
      baseline.py          Reha_Crane_baseline.ipynb 어댑터
      coop.py              Reha_Crane_predetermined.ipynb 어댑터
      rules.py             휴리스틱 공용 함수 (작업 분해 · 제약 검증 · 목적값). 2015 논문 기반 LonRem · RemReq · RH+LonRem 클래스도 남아 있으나 2026-10-19 비교 목록에서 뺌
      window_rule.py       Window–Slack 지원 규칙 휴리스틱
      rolling.py           롤링 호라이즌 (정보 공개 시점마다 재계획 · 확정 · 실행)
    instance_sets.py       실험 인스턴스 세트 정의 (5시간: 균형 27 + 극단 16 · 10시간: 균형 18 + 대규모 6 + 몰림 20)
    nbchart.py             휴리스틱 결과를 노트북 시각화 셀로 그리기 (노트북 원본 차트 카드)
  experiments/run_experiments.py  비교 실험 일괄 실행 (소형 · 부하 격자 · 실데이터)
  experiments/run_rolling.py      롤링 호라이즌 실험 (AGV 정보 시간 H × 부하, --set 으로 실험 세트)
  experiments/make_instance_sets.py  실험 세트 파일 만들기 (data/instance_sets/<세트>/)
  tests/test_pipeline.py   전 구간 점검
  tests/test_rules.py      휴리스틱 제약·규칙 점검
```

## 8. 문제 해결

**콘솔에 `A module that was compiled using NumPy 1.x cannot be run in NumPy 2.x` 가 뜬다**
서버를 띄운 파이썬에 numpy 2.x 와 numpy 1.x 용으로 빌드된 matplotlib 이 섞여 있는 것이다.
해(최적화)는 정상이고 노트북 원본 차트만 건너뛴다 (화면 상세 메모에 안내가 뜬다).

```bash
which python                      # 서버를 띄운 파이썬 확인
python -m pip install -U matplotlib
# 또는 Jupyter 노트북 커널과 같은 파이썬으로 실행
#   (노트북에서 import sys; print(sys.executable) 로 경로 확인)
```

**코드를 업데이트했는데 화면이 이상하거나 새 버튼이 안 보인다 / '생성 실패' 가 뜬다**
- 이미 열어 둔 탭은 옛 화면 그대로다 → 새로고침(⌘R). 화면 위에 노란 띠
  "화면 파일이 업데이트됐습니다" 가 뜨면 [새로고침] 을 누르면 된다 (10초마다 확인).
- 파이썬 코드는 서버를 재시작해야 반영된다 (Ctrl+C 후 `python run_server.py`).
  서버가 켜진 뒤 바뀐 .py 가 있으면 노란 띠에 "서버 재시작이 필요합니다" 와 파일 이름이 뜬다.

**`OSError: [Errno 48] Address already in use`**
이전에 띄운 서버가 다른 터미널(VS Code 터미널 탭 등)에 아직 살아 있는 것이다.
이제 `run_server.py` 가 시작할 때 알아서 처리한다.

| 포트를 잡고 있는 것 | 동작 |
|---|---|
| 이전 웹 서버 (`run_server.py`), 돌고 있는 실험 없음 | 종료하고 같은 포트(8766)로 다시 띄움 |
| 이전 웹 서버인데 실험이 실행/대기 중 | 건드리지 않고 다음 빈 포트(8767 …)로 띄움 |
| 다른 프로그램 | 건드리지 않고 다음 빈 포트로 띄움 (PID·명령 출력) |

이전 서버를 그대로 두고 싶으면 `python run_server.py --keep-old`.
직접 정리하려면 `lsof -nP -iTCP:8766 -sTCP:LISTEN` 으로 PID 를 보고 `kill <PID>`.

## 9. 실데이터로 실험하기

### 실데이터 가져오기 (실행 탭 → 인스턴스 → "실데이터 가져오기")

연구 폴더 `data/` 의 원본 CSV(`WAS_ARMG_CAPA_202501_202503.csv`, 91일)를 읽어 블록 · 날짜 · 시간 구간을 인스턴스로 만든다.
원본이 없으면 후보 블록 추출 파일(`*_paired_crane_full_day.csv`)을 쓴다. 다른 폴더는 `python run_server.py --realdata 폴더`.
처음 한 번 날짜별 색인(`web/data/realcache/`)을 만든다 (원본이 바뀌면 다시 만듦).

| 항목 | 규칙 |
|---|---|
| 작업 순서 | 구간 안에서 시작한 DS·LD·GI·GO 를 크레인별 실제 시작 순서대로 (= predetermined sequence) |
| 크레인 | 홀수호기 SC, 짝수호기 LC. 크레인-유형이 맞지 않는 작업(예: SC 의 GO)은 빼고 개수 표시 |
| bay | DS·GI 는 TO_BAY, LD·GO 는 FM_BAY. 야드 bay 수 = 그 블록에서 관측된 최대 bay |
| GO rehandle | RH_CNT. 같은 크레인의 직전 AH 가 복원되면 그 TO_BAY 를 rh_d 로 (다른 블록으로 옮긴 것은 같은 bay 안으로) |
| LD rehandle | 화면의 "LD rehandle" 선택. **자동**(기본): RH_CNT 가 기록된 LD(2025-02-08 12:04 부터)는 기록, 기록이 빈 LD 는 직전에 같은 크레인이 같은 블록·bay·row 에서 연달아 한 AH(간격 ≤ 10분)로 추정 — 기록과 개수 일치 99.3 %, 발생 정밀도 96.7 %, 재현율 87.0 %. **기록만**: 기록이 빈 LD 는 0. **넣지 않음**: LD rehandle 0 (예전 동작). 통계는 `analysis/ld_rehandle/ld_rehandle_report.html` |
| 트럭 도착 a | REQ_DT − 구간 시작 (초). 구간 전에 요청된 트럭은 0 |
| 시작 위치 | 구간 직전 마지막 작업의 도착 위치. 안전거리보다 가까우면 벌려 놓음 |
| 시간 파라미터 | 초 단위: t̂ 1.5 · pick 75 · drop 75 · ρ 150. 안전 여유 3 은 **가정값** |
| 실제 기록 | 같은 구간의 실제 makespan · 트럭 대기를 같은 정의로 계산 → 비교표에 "실제 기록 (참고)" 행 |

시간 막대그래프(시간대별 본 작업 수 · rehandle 수 GO+LD)를 눌러 시작 시각을 고를 수 있다. 1~2시간 구간을 권장한다.

### 생성기 보정 ("실데이터 보정값 채우기")

2시간 분량, 초 단위. DS 6 · LD 4 · GI 4 · GO 4, bay 85, GO rehandle 확률 0.52, LD 0.035, 개수 분포 = 실데이터
(GO 1: 34.4 % · 2: 27.2 % · 3: 18.0 % · 4: 13.0 % · 5: 7.5 %, LD 1: 70.7 % · 2: 16.3 % · 3: 7.8 % · 4: 3.6 % · 5: 1.5 %), 다른 bay 로 옮길 확률 0.7, 거리 상한 10,
트럭 간격 SC 584초 · LC 739초. 근거는 `model/model_formulation_v1.md` 의 "실험 설정" 절.
