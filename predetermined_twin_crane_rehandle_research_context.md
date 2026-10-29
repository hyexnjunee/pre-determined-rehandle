# Predetermined-Sequence Twin-Crane Scheduling with Cooperative Rehandling
## 대화 기반 연구 컨텍스트 및 데이터 분석 요약

> 이 문서는 현재 대화창에서 논의된 연구 목적, 데이터 해석, 재검증된 분석 결과, 후보 실험 데이터, 주의사항 및 향후 모델링 방향을 다른 AI가 그대로 이어서 이해할 수 있도록 정리한 컨텍스트 문서이다.
>
> **중요:** 아래 수치 중 `5,300건`, `12,143개` 등은 원본 CSV를 다시 확인하여 수정된 **최종 재검증 값**이다. 이전 대화 중 등장한 `5,302건`, `12,148개`는 사용하지 않는다.

---

# 1. 연구 목적

사용자는 **Predetermined Sequence 상황의 Twin Yard Crane Scheduling**을 연구하려고 한다.

핵심 아이디어는 다음과 같다.

- 하나의 야드 블록에 두 대의 크레인이 존재한다.
- **Landside crane**은 주로 외부 트럭 관련 반입/반출 작업(`GI`, `GO`)을 수행한다.
- **Seaside crane**은 주로 본선 관련 양하/적하 작업(`DS`, `LD`)을 수행한다.
- 실제 데이터를 보면 외부 트럭의 **반출 작업(`GO`)**을 수행할 때, 목표 컨테이너 위에 적재된 컨테이너를 먼저 옮겨야 하는 **Rehandle 작업**이 상당히 많이 발생한다.
- 현재 실제 운영에서는 해당 Rehandle을 주로 Landside crane이 직접 수행한다.
- 연구에서는 Landside crane이 GO를 수행하기 전에 필요한 Rehandle 작업 중 일부를 **반대편 Seaside crane이 대신 수행하도록 협업**시키는 Twin-Crane Scheduling을 고려한다.
- 단, 두 크레인의 기존 작업 순서는 **Predetermined Sequence**로 주어진 상황을 기본으로 한다.

연구의 핵심 질문은 다음과 같다.

1. 실제 항만 야드 블록에서 GO 작업에 Rehandle이 충분히 자주 발생하는가?
2. Rehandle이 실제 GO 시작 지연 및 트럭 체류시간에 영향을 주는가?
3. Landside crane이 Rehandle을 수행하는 동안 Seaside crane에 유휴시간이 실제로 존재하는가?
4. 그 유휴시간을 이용하여 Seaside crane이 Rehandle을 도와줄 수 있는가?
5. 이동시간, non-crossing, safety distance, 기존 predetermined jobs까지 고려해도 협업이 가능한가?
6. 협업 시 GO completion time, truck waiting/TAT, makespan, crane travel 등이 얼마나 개선되는가?

---

# 2. 사용 데이터

주요 원본 파일:

- `WAS_ARMG_CAPA_202501_202503_extracted.csv`
- `schedule_gantt.html`

HTML은 CSV 데이터를 보기 좋게 시각화한 파일이며, 크레인 짝과 작업 타입 및 시간축 해석에 사용했다.

## 2.1 실제 데이터 범위

CSV 전체 레코드:

- **71,385개 작업 레코드**

실제 작업 시각 범위:

- 대략 **2024-12-31 23시경 ~ 2025-01-11 22시경**

따라서 파일명은 `202501_202503`이지만, 현재 제공된 파일에 실제로 포함된 작업은 약 11일 범위이다.

**주의:** 이 데이터만을 가지고 “2025년 1~3월 전체 3개월 결과”라고 해석하면 안 된다.

---

# 3. 블록과 Twin Crane 구조

HTML의 매핑 로직:

```text
207 / 208 -> A04
209 / 210 -> A05
...
```

즉 하나의 블록마다 연속된 두 개 크레인이 존재한다.

대화에서 사용한 기준:

- **홀수호기 = Seaside crane**
- **짝수호기 = Landside crane**

예:

```text
A04
Seaside  : 207
Landside : 208
```

HTML 설명에서도 다음과 같이 두 기를 함께 표시한다.

- 해측 = 홀수호기
- 육측 = 짝수호기

---

# 4. 주요 JOB_TYPE 해석

HTML 기준 주요 작업 타입:

| 코드 | 의미 |
|---|---|
| `DS` | 양하 |
| `LD` | 적하 |
| `GI` | 반입 |
| `GO` | 반출 |
| `AH` | 자동이적 / 실제 GO Rehandle과 강하게 연결되는 작업 |
| `RH` | 재조작 작업 타입 |
| 기타 | MI, MO, LC, ETC 등 |

중요한 발견:

**GO의 `RH_CNT`에 기록된 Rehandle은 대부분 `JOB_TYPE=RH` 행이 아니라, 해당 GO 직전에 수행되는 `AH` 작업열로 나타난다.**

따라서 GO Rehandle을 분석할 때는 단순히 `RH` 타입만 세면 안 된다.

---

# 5. GO와 Rehandle 발생 빈도

전체 GO 작업:

- **10,812건**

GO를 `RH_CNT`별로 집계한 결과:

| RH_CNT | GO 건수 | 전체 GO 비율 |
|---:|---:|---:|
| 0 | 5,195 | 48.05% |
| 1 | 1,930 | 17.85% |
| 2 | 1,526 | 14.11% |
| 3 | 1,009 | 9.33% |
| 4 | 731 | 6.76% |
| 5 | 421 | 3.89% |

따라서:

```text
RH_CNT > 0 GO = 5,617건
```

전체 GO 대비:

```text
5,617 / 10,812 = 51.95%
```

즉 **전체 GO의 약 51.95%에서 최소 1회의 Rehandle이 필요하다.**

전체 GO에 기록된 `RH_CNT` 합:

```text
13,038개
```

Rehandle이 발생한 GO 한 건당 평균 상단 컨테이너 수:

```text
13,038 / 5,617 ≈ 2.32개
```

따라서 실제 현장에서 반출 Rehandle은 드문 예외가 아니라, 연구할 가치가 충분한 빈도로 발생한다.

---

# 6. 실제 Rehandle 작업열 복원

GO에 `RH_CNT > 0`이 있을 때, 같은 크레인의 직전 작업들을 확인했다.

복원 규칙:

> GO 직전 `RH_CNT`개의 작업이 모두 `AH`이고,  
> 해당 AH들의 출발 Block/Bay가 GO의 출발 Block/Bay와 동일한지 확인.

예시:

```text
A22 / Crane 244 / GO / RH_CNT = 5
```

이 GO 직전에 같은 244호기가 동일 Bay를 출발점으로 하는 AH를 5개 연속 수행한 뒤 GO를 수행하는 패턴이 관찰되었다.

## 6.1 최종 재검증 결과

`RH_CNT > 0` GO:

```text
5,617건
```

엄격히 `WRK_START_DT` 기준으로 같은 크레인의 직전 작업을 검사했을 때 정확히 복원된 사건:

```text
5,300건
```

복원율:

```text
5,300 / 5,617 = 94.36%
```

**최종 사용값 = 5,300건 / 94.36%**

이전 대화 중 `5,302건 / 94.39%`라는 값이 있었지만 재검증 후 수정되었다.

## 6.2 RH_CNT별 복원율

기존 분석에서 관찰된 대략적인 경향:

| RH_CNT | 직전 AH 패턴 일치율 |
|---:|---:|
| 1 | 약 96% |
| 2 | 약 95% |
| 3 | 약 93% |
| 4 | 약 92% |
| 5 | 약 88% |

RH_CNT가 커질수록 예외가 조금 늘어나지만 전체적으로 매우 높은 일치율을 보인다.

## 6.3 복원된 AH 개수

복원된 Rehandle AH:

```text
12,143개
```

전체 `RH_CNT` 합 13,038 대비:

```text
12,143 / 13,038 = 93.14%
```

**최종 사용값 = 12,143개 / 93.14%**

이전 대화 중 `12,148개 / 약 93.2%`라는 값이 있었으나 재검증 후 수정되었다.

---

# 7. Rehandle과 GO 지연

GO 작업 자체의 `WRK_START_DT -> COMP_DT`만 보면 RH가 많다고 반드시 길어지는 형태가 뚜렷하지 않았다.

이유:

- Rehandle AH는 대부분 GO의 `WRK_START_DT` **이전**에 이미 수행된다.
- 따라서 GO 자체 처리시간만 보면 Rehandle 영향이 빠진다.

따라서 분석해야 할 지표는:

```text
REQ_DT -> WRK_START_DT
REQ_DT -> COMP_DT
TAT
```

## 7.1 RH_CNT별 중앙값

| RH_CNT | REQ → GO 개시 중앙값 | REQ → GO 완료 중앙값 | TAT 중앙값 |
|---:|---:|---:|---:|
| 0 | 14.4분 | 20.5분 | 23.2분 |
| 1 | 22.0분 | 27.6분 | 28.3분 |
| 2 | 25.6분 | 31.0분 | 32.3분 |
| 3 | 30.0분 | 35.4분 | 37.1분 |
| 4 | 31.8분 | 37.7분 | 38.6분 |
| 5 | 38.4분 | 44.7분 | 44.6분 |

RH_CNT=0과 RH_CNT=5를 비교하면:

```text
REQ -> GO START
14.4분 -> 38.4분
약 +24분
```

즉 Rehandle이 많을수록 GO 개시가 늦어지는 매우 뚜렷한 패턴이 존재한다.

다만 이는 **관계/연관성**이며 바로 인과관계라고 단정하면 안 된다.

가능한 교란요인:

- 특정 시간대 혼잡
- 블록 내 장치 상태
- 트럭 집중 도착
- 다른 크레인 작업량
- 본선 작업 집중

---

# 8. Block × Date 단위 재검증

특정 블록이나 특정 날짜 때문에 전체 패턴이 생긴 것인지 확인하기 위해 `Block × 날짜` 단위로 RH=0과 RH>0을 비교했다.

비교가 가능한 그룹:

```text
227개
```

그중 RH>0 GO의 `REQ -> START` 중앙값이 RH=0보다 더 길었던 그룹:

```text
91.6%
```

두 그룹 간 중앙값 차이의 중앙값:

```text
약 8.6분
```

따라서 Rehandle과 GO 지연의 관계는 일부 블록 하나에만 국한된 현상은 아니다.

---

# 9. 반대편 Seaside Crane의 Idle 분석

연구 아이디어의 핵심은 Landside crane의 GO Rehandle을 Seaside crane이 도와주는 것이다.

## 9.1 Idle 판정 기준

각 복원 Rehandle 사건의 시간 구간을:

```text
첫 번째 Rehandle AH의 WRK_START_DT
~
마지막 Rehandle AH의 COMP_DT
```

로 잡는다.

그 시간 동안 **반대편 크레인의 모든 작업 레코드**와 중첩을 검사한다.

중요:

Idle 계산에서 제외한 작업 타입은 없다.

다음 작업들이 모두 busy 판정에 포함된다.

- DS
- LD
- GI
- GO
- AH
- RH
- MI
- MO
- LC
- ETC 및 기타 기록된 작업

즉 **Seaside의 DS/LD를 무시한 뒤 idle이라고 계산한 것이 아니다.**

## 9.2 전체 복원 Rehandle 5,300건 기준

Rehandle 구간 전체에서 반대편 크레인의 작업과 단 한 번도 겹치지 않은 사건:

```text
2,219건
```

비율:

```text
2,219 / 5,300 = 41.87%
```

즉:

**41.9%의 복원 Rehandle 사건에서는 반대편 크레인이 해당 Rehandle 구간 전체 동안 기록상 어떠한 작업도 수행하지 않았다.**

Rehandle 구간의 50% 이상이 idle:

```text
3,165건
```

비율:

```text
3,165 / 5,300 = 59.72%
```

일부라도 반대 크레인 작업과 겹친 사건:

```text
3,081건
```

비율:

```text
58.13%
```

---

# 10. 반대편 크레인은 실제로 DS/LD 중심인가?

그렇다.

홀수호기, 즉 Seaside crane의 전체 작업은 대략:

```text
DS : 13,326건
LD : 8,866건
AH : 9,269건
RH : 1,374건
GO : 444건
GI : 18건
```

즉 Seaside 쪽은 실제로 DS/LD 본선 작업을 상당히 많이 수행한다.

또 Rehandle이 붙은 GO 대부분은 Landside인 짝수호기에서 발생했다.

분석 당시:

```text
Rehandle 관련 GO 중 약 96.5%가 짝수호기
```

로 확인되었다.

따라서 실제 연구 구조인:

```text
Landside GO + Rehandle
        ↓
Seaside가 일부 Rehandle 지원
```

과 데이터의 실제 운영 구조가 상당히 잘 맞는다.

---

# 11. Rehandle 구간과 겹친 Seaside 작업 종류

반대편 크레인이 busy였던 경우 실제 어떤 작업과 겹쳤는지 확인한 결과, 중첩 빈도는 다음과 같았다.

| Seaside 작업 타입 | Rehandle 구간과의 중첩 횟수 |
|---|---:|
| DS | 3,025 |
| LD | 1,516 |
| AH | 1,474 |
| RH | 300 |
| MI | 58 |
| MO | 38 |
| LC | 1 |

즉 busy 판정에서 가장 큰 부분은 실제로 **DS와 LD**가 차지한다.

따라서 41.9% 완전 idle이라는 결과는 DS/LD를 제외해서 만들어진 값이 아니다.

---

# 12. 데이터 완전성 관련 주의사항

원본 데이터의 시간 필드 존재율도 확인했다.

대략:

```text
WRK_START_DT 존재율 ≈ 99.95%
COMP_DT 존재율      ≈ 99.82%
```

홀수 크레인 쪽에서 시작/완료 시각이 부족한 행은 매우 소수였다.

따라서 연구 문장에서는:

> “Seaside crane이 idle이었다”

보다

> **“recorded schedule 기준으로 해당 시간 동안 반대편 크레인에 기록된 작업이 없었다”**

라고 표현하는 것이 더 엄밀하다.

---

# 13. 41.9%의 정확한 의미

잘못된 표현:

> “41.9%에서는 Seaside crane이 실제로 Rehandle을 도와줄 수 있었다.”

아직 이렇게 말하면 안 된다.

정확한 표현:

> **“전체 복원 Rehandle 사건의 41.9%에서 반대편 크레인은 해당 Rehandle 구간 전체 동안 기록상 다른 작업을 수행하지 않았다.”**

즉 이 값은:

```text
Temporal Feasibility
```

를 의미한다.

아직 고려하지 않은 요소:

- Seaside crane의 현재 위치
- Rehandle Bay까지 travel time
- 두 크레인의 non-crossing
- safety margin / interference
- Seaside crane의 다음 predetermined job
- 다음 DS/LD 시작시각 지연 여부
- rehandle pick/drop 수행 위치
- crane별 작업 가능 영역
- simultaneous operation feasibility

따라서 다음 단계에서는 **Spatial + Sequence Feasibility**를 추가해야 한다.

---

# 14. Rehandle 단일 작업의 실제 처리시간

복원된 Rehandle AH 하나의 실제 처리시간 중앙값:

```text
약 177초
≈ 3분
```

따라서 단순 temporal upper-bound 분석에서는 Seaside crane에 약 3분 이상의 연속 idle gap이 존재한다면 Rehandle 1개를 수행할 시간적 가능성이 있다고 볼 수 있다.

기존 분석에서는 이동시간을 제외한 단순 시간 기준으로 약 **58%의 사건에서 최소 Rehandle 1개를 수행할 수 있을 정도의 연속 idle gap**이 관찰되었다.

다만 이 58% 역시 **실제 협업 가능률이 아니라 temporal upper bound**이다.

---

# 15. 실험 후보 Date × Block 선정

사용자는 실제 연구/실험용으로:

> Landside crane이 GO/Rehandle을 많이 수행하고, Seaside crane이 DS/LD를 수행하면서도 Rehandle을 도와줄 수 있는 idle 구간이 있는 날짜와 블록

을 찾고자 했다.

후보 선정에서는 단순 idle 비율만 높은 날이 아니라:

1. Landside GO가 충분히 존재
2. Rehandle 발생이 충분
3. Seaside가 실제 DS/LD를 수행
4. 그럼에도 Seaside idle window가 존재

하는 날을 골랐다.

---

# 16. 추천 실험 후보 4개

| 후보 | 날짜 / Block | Seaside → Landside | Landside GO | GO+RH | 복원 RH 사건 | RH 개수 | Seaside 완전 idle | RH구간 50% 이상 idle | Seaside DS+LD |
|---|---|---|---:|---:|---:|---:|---:|---:|---:|
| 1 | **2025-01-06 / A24** | **247 → 248** | 70 | 44 | **44** | **122** | **39/44 = 88.6%** | **43/44 = 97.7%** | 60 |
| 2 | **2025-01-10 / A13** | **225 → 226** | 88 | 49 | **48** | **117** | **25/48 = 52.1%** | **37/48 = 77.1%** | 57 |
| 3 | **2025-01-09 / A12** | **223 → 224** | 73 | 40 | **39** | 80 | **23/39 = 59.0%** | **30/39 = 76.9%** | **95** |
| 4 | **2025-01-07 / A22** | **243 → 244** | 76 | 42 | **39** | 97 | **19/39 = 48.7%** | **27/39 = 69.2%** | **103** |

---

# 17. 후보별 의미

## 17.1 A24 / 2025-01-06 — Proof-of-Concept 후보

Twin crane:

```text
Seaside  : 247
Landside : 248
```

Landside 248:

```text
GO = 70건
GO with RH = 44건
복원 RH 사건 = 44건
총 RH = 122개
```

Seaside 247:

```text
DS + LD = 60건
```

그럼에도:

```text
완전 idle RH 사건 = 39 / 44 = 88.6%
50% 이상 idle       = 43 / 44 = 97.7%
```

따라서 협업 알고리즘이 제대로 작동하는지 확인하기 위한 **Low-load / High-assistance Proof-of-Concept** 데이터로 적합하다.

단점:

- 너무 협업하기 쉬운 사례일 수 있다.
- 이 사례 하나만 쓰면 “Seaside가 원래 한가해서 가능한 것 아닌가?”라는 질문을 받을 수 있다.

---

# 18. A13 / 2025-01-10 — Medium Case

Twin crane:

```text
Seaside  : 225
Landside : 226
```

주요 값:

```text
Landside GO          = 88
GO with RH           = 49
복원 RH 사건         = 48
총 RH                = 117
Seaside DS+LD        = 57
완전 idle            = 25/48 = 52.1%
50% 이상 idle        = 37/48 = 77.1%
```

RH 양이 많으면서 Seaside workload와 협업 여지가 적절하게 섞여 있는 **중간 난이도** 후보이다.

---

# 19. A12 / 2025-01-09 — High Seaside Operation Case

Twin crane:

```text
Seaside  : 223
Landside : 224
```

주요 값:

```text
Landside GO          = 73
GO with RH           = 40
복원 RH 사건         = 39
총 RH                = 80
Seaside DS+LD        = 95
완전 idle            = 23/39 = 59.0%
50% 이상 idle        = 30/39 = 76.9%
```

Seaside DS/LD가 **95건**으로 상당히 많으면서도 협업 가능성이 관찰된다.

따라서:

> “본선 작업량이 많은 Seaside crane에서도 협업 여지가 존재한다.”

는 것을 보여주기 좋은 후보이다.

---

# 20. A22 / 2025-01-07 — Stress Case

Twin crane:

```text
Seaside  : 243
Landside : 244
```

주요 값:

```text
Landside GO          = 76
GO with RH           = 42
복원 RH 사건         = 39
총 RH                = 97
Seaside DS+LD        = 103
완전 idle            = 19/39 = 48.7%
50% 이상 idle        = 27/39 = 69.2%
```

Seaside가:

```text
DS + LD = 103건
```

으로 매우 바쁜 편이다.

그럼에도:

```text
48.7% 사건은 RH 구간 전체에서 recorded schedule 기준 idle
69.2% 사건은 RH 구간의 절반 이상 idle
```

이므로 실제 운영 workload가 높은 조건에서 협업 효과를 시험하는 **Stress Case**로 매우 유용하다.

---

# 21. 권장 실험 시나리오

논문 실험에서 4개 후보를 다음처럼 구분하는 것이 좋다.

```text
A24 / 2025-01-06
= Low-load / High-assistance
= Proof-of-Concept

A13 / 2025-01-10
= Medium workload / Medium difficulty

A12 / 2025-01-09
= High Seaside DS/LD workload

A22 / 2025-01-07
= Stress Case
= Very high Seaside workload
```

하나의 좋은 사례만 사용하는 것보다 workload 수준이 다른 여러 실제 사례를 사용하면 연구의 설득력이 높아진다.

---

# 22. 추출해 둔 실험 파일

후보 4개에 대해 각 블록의 **두 크레인 하루 전체 작업**을 추출했다.

## 전체 묶음

```text
twin_crane_candidate_blocks.zip
```

## 후보 요약

```text
candidate_summary.csv
```

## A24 / 2025-01-06

```text
20250106_A24_paired_crane_full_day.csv
20250106_A24_rehandle_events.csv
```

Twin crane:

```text
247 / 248
```

## A13 / 2025-01-10

```text
20250110_A13_paired_crane_full_day.csv
20250110_A13_rehandle_events.csv
```

Twin crane:

```text
225 / 226
```

## A12 / 2025-01-09

```text
20250109_A12_paired_crane_full_day.csv
20250109_A12_rehandle_events.csv
```

Twin crane:

```text
223 / 224
```

## A22 / 2025-01-07

```text
20250107_A22_paired_crane_full_day.csv
20250107_A22_rehandle_events.csv
```

Twin crane:

```text
243 / 244
```

---

# 23. `paired_crane_full_day.csv`의 용도

이 파일은 Rehandle만 추출한 것이 아니다.

해당 날짜/블록의 Twin crane 두 대가 하루 동안 수행한 **전체 작업**을 포함한다.

즉:

```text
DS
LD
GI
GO
AH
RH
기타 작업
```

을 함께 가지고 있기 때문에 향후 Predetermined Sequence를 구성하는 입력 데이터로 사용할 수 있다.

---

# 24. `rehandle_events.csv`의 용도

각 GO-Rehandle 사건에 대해 협업 분석에 필요한 이벤트 단위 정보를 정리하기 위한 파일이다.

대화에서 넣기로 한 핵심 정보:

- 날짜
- Block
- Landside crane
- Seaside crane
- GO 작업
- GO Bay
- RH_CNT
- Rehandle 시작시각
- Rehandle 종료시각
- 실제 복원된 AH 목록
- Rehandle별 목적 Bay
- Rehandle processing time
- Seaside overlap 여부
- Seaside idle ratio
- 최대 연속 idle gap
- completely idle 여부
- 겹친 Seaside 작업 종류

향후 Spatial Feasibility 분석에서 추가할 정보:

- Seaside crane의 RH 시작 전 위치
- RH Bay까지 travel time
- 다음 predetermined job의 시작 위치
- RH 수행 후 다음 job까지 travel time
- safety/non-crossing 위반 여부
- 다음 job delay 여부
- assist 가능 여부 binary

---

# 25. 향후 실제 Cooperative Feasibility 판정

현재는 Temporal Feasibility까지만 확인했다.

다음 단계에서는 각 Rehandle 사건에 대해 아래를 계산해야 한다.

## 25.1 Time feasibility

Seaside crane에 충분한 idle gap이 존재하는가?

예:

```text
available_idle >= travel_to_RH
                + RH_processing
                + travel_to_next_job
```

## 25.2 Spatial feasibility

Seaside 현재 위치에서 해당 GO Bay까지 갈 수 있는가?

## 25.3 Non-crossing constraint

두 크레인의 위치 관계를 유지할 수 있는가?

예를 들어 Seaside < Landside와 같은 ordering이 물리적으로 유지되어야 한다.

## 25.4 Safety distance

두 크레인 간 최소 안전 Bay 간격을 만족하는가?

## 25.5 Predetermined sequence preservation

Seaside crane의 기존 DS/LD 등의 순서를 바꾸지 않는다.

즉:

```text
...
DS_i
[optional assisted RH]
LD_j
...
```

처럼 기존 job sequence 사이의 idle gap에 RH를 삽입하는 방향이 자연스럽다.

## 25.6 No-delay condition

초기 연구에서는 특히 다음 조건을 둘 수 있다.

> Seaside crane이 RH를 도와도 원래 예정된 다음 predetermined job의 시작시간을 늦추지 않는 경우에만 assistance 허용.

이렇게 하면 실제 현장 적용 측면에서 매우 직관적인 협업 정책이 된다.

---

# 26. 초기 Mathematical Modeling 방향

기존 Predetermined Sequence Twin Crane Scheduling에 **optional assisted rehandle**을 추가하는 구조가 가능하다.

각 GO \(j\)에 필요한 Rehandle set:

```text
R_j
```

각 Rehandle \(r \in R_j\)에 대해:

```text
a_r = 1
```

이면 Seaside가 지원,

```text
a_r = 0
```

이면 기존 Landside가 수행.

주요 제약:

1. 기존 Landside job sequence 유지
2. 기존 Seaside job sequence 유지
3. Rehandle은 해당 GO보다 선행
4. 지원 RH는 Seaside의 두 predetermined jobs 사이에 삽입
5. 크레인 간 non-crossing
6. safety distance
7. 이동시간 반영
8. 동일 시간 한 크레인 한 작업
9. 필요 시 simultaneous RH 허용/금지
10. 협업 후 다음 predetermined job 지연 제한

---

# 27. 고려 가능한 목적함수

연구 목표에 따라 다음을 사용할 수 있다.

## Option A: GO completion / Truck waiting 중심

```text
Min Σ GO completion time
```

또는

```text
Min Σ truck waiting time
```

## Option B: Makespan

```text
Min C_max
```

## Option C: Multi-objective

예:

```text
Min
α × Total truck waiting
+ β × Makespan
+ γ × Crane travel
```

또는 lexicographic 방식으로:

```text
1. Truck waiting 최소
2. Makespan 최소
3. Travel 최소
```

---

# 28. 연구 스토리라인

논문 또는 발표에서 권장되는 논리 흐름:

## Step 1. 현장 문제 발견

외부 트럭의 GO 작업에서 Rehandle이 빈번하게 발생한다.

```text
GO 중 51.95%에서 RH_CNT > 0
```

## Step 2. Rehandle의 실제 작업 형태 확인

GO의 RH_CNT를 실제 작업열의 AH로 복원할 수 있다.

```text
5,617건 중 5,300건
94.36%
```

## Step 3. 운영 성능 영향 확인

RH_CNT가 증가할수록 GO 개시 및 TAT가 증가한다.

예:

```text
REQ -> START
RH0 = 14.4분
RH5 = 38.4분
```

## Step 4. Twin crane collaboration opportunity

Landside가 RH를 수행하는 동안 반대편 Seaside가 recorded schedule 기준 완전히 idle인 사건이 상당수 존재한다.

```text
2,219 / 5,300
= 41.87%
```

또:

```text
50% 이상 idle
= 59.72%
```

## Step 5. 연구 아이디어

Seaside의 기존 DS/LD sequence를 유지하면서 idle gap에 일부 RH를 삽입한다.

## Step 6. 최적화

Predetermined Sequence Twin-Crane Scheduling with Cooperative Rehandling 모델을 개발한다.

## Step 7. 평가

Baseline:

```text
Landside performs all GO rehandles
```

Proposed:

```text
Seaside assists selected GO rehandles
```

비교:

- Truck waiting
- GO completion
- TAT
- Makespan
- Crane travel
- Seaside job delay
- Number of assisted RH
- Assistance utilization

---

# 29. 표현상 주의

다음 표현은 피한다.

```text
“Rehandle이 GO 지연을 유발한다.”
```

현재 데이터만으로 인과성을 완전히 증명한 것은 아니다.

대신:

```text
“Rehandle 발생 및 RH_CNT 증가와 GO service delay 사이에
일관된 양의 관계가 관찰되었다.”
```

라고 표현하는 것이 적절하다.

또:

```text
“41.9%에서 반대편 크레인이 도와줄 수 있었다.”
```

도 아직 과한 표현이다.

대신:

```text
“41.9%의 복원 Rehandle 사건에서 반대편 크레인은
해당 Rehandle 구간 전체 동안 recorded schedule 기준으로
다른 작업을 수행하지 않았다.”
```

라고 표현한다.

---

# 30. 반드시 기억해야 할 최종 핵심 수치

```text
전체 작업 레코드                 = 71,385
GO                              = 10,812

GO with RH_CNT > 0              = 5,617
GO Rehandle 발생률               = 51.95%

전체 RH_CNT 합                   = 13,038
RH 발생 GO당 평균 RH             = 2.32개

AH 작업열로 복원된 GO-RH 사건    = 5,300
복원율                           = 94.36%

복원된 AH                        = 12,143
전체 RH_CNT 대비 설명률           = 93.14%

반대편 크레인 완전 idle 사건      = 2,219 / 5,300
완전 idle 비율                    = 41.87%

반대편 크레인 50% 이상 idle       = 3,165 / 5,300
비율                              = 59.72%

Rehandle AH 1건 처리시간 중앙값    ≈ 177초
```

---

# 31. 이전 값 중 사용하면 안 되는 값

아래 값은 초기 계산 후 수정되었으므로 최종 분석에 사용하지 않는다.

```text
5,302건 → 사용 금지
5,300건 → 최종

12,148개 → 사용 금지
12,143개 → 최종
```

---

# 32. 현재 가장 권장되는 다음 작업

가장 먼저:

```text
2025-01-06 / A24
Seaside 247
Landside 248
```

를 사용하여 24시간 timeline을 구성한다.

표시할 내용:

1. 247의 DS/LD 전체 작업
2. 248의 GI/GO 전체 작업
3. 248의 GO 전 Rehandle AH
4. 각 Rehandle 발생 구간
5. 같은 시간 247의 idle gap
6. 247의 당시 위치
7. 다음 predetermined DS/LD
8. 247이 RH를 대신 수행했을 때의 가상 timeline

그 다음:

```text
A24 → A13 → A12 → A22
```

순으로 난이도를 증가시켜 proposed method를 테스트한다.

---

# 33. 연구의 한 문장 정의

> **Predetermined job sequences를 유지하는 twin yard crane 환경에서, landside crane의 outbound retrieval 작업에 수반되는 rehandling을 seaside crane의 유휴시간을 활용하여 선택적으로 분담함으로써 truck service delay와 yard crane operating time을 개선하는 cooperative twin-crane scheduling problem을 연구한다.**

---

# 34. AI가 이후 답변할 때 유지할 전제

이 문서를 이후 AI에게 제공할 경우 다음을 기본 전제로 삼는다.

- 연구 대상은 일반 Twin Crane이 아니라 **Predetermined Sequence Twin Yard Crane Scheduling**이다.
- 협업의 핵심은 모든 작업 공유가 아니라 **Landside GO의 Rehandle assistance**이다.
- Seaside는 기존 DS/LD 역할을 유지한다.
- Seaside의 기존 predetermined sequence는 가급적 변경하지 않는다.
- RH는 GO보다 반드시 선행해야 한다.
- 실제 GO Rehandle은 대부분 `RH` 타입이 아니라 GO 직전의 `AH` 작업열로 복원된다.
- 현재 `41.87%`는 **실제 협업 가능률이 아니라 Temporal Idle Opportunity**이다.
- 최종 feasibility에는 위치, travel, non-crossing, safety distance, next-job delay를 추가해야 한다.
- 실제 데이터 후보는 A24, A13, A12, A22를 우선 사용한다.
- 가장 쉬운 PoC는 A24 / 2025-01-06이다.
- 현실적인 busy case 및 stress case는 A12 / 2025-01-09와 A22 / 2025-01-07이다.
