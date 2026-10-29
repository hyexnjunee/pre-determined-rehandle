"""
무작위 인스턴스 생성기
======================

실험용 인스턴스를 같은 규칙으로 여러 개 만든다. 시드를 주면 똑같이 다시 만들 수 있다.

생성 규칙
---------
- SC 순서 = DS n_ds 개 + LD n_ld 개를 섞은 것, LC 순서 = GI n_gi 개 + GO n_go 개를 섞은 것
- 작업 bay 는 야드 1 ~ n_bay 에서 균등하게 뽑는다
- GO 는 확률 rh_prob_go, LD 는 rh_prob_ld 로 rehandle 이 붙는다 (예전 rh_prob 는 둘 다에 적용)
- 개수는 rh_dist="uniform" 이면 1 ~ rh_max 균등, "data" 면 원본 CSV 의 RH_CNT 분포를
  rh_max 에서 잘라 다시 정규화
    GO (1: 34.4 %, 2: 27.2 %, 3: 18.0 %, 4: 13.0 %, 5: 7.5 %)
    LD (1: 70.7 %, 2: 16.3 %, 3: 7.8 %, 4: 3.6 %, 5: 1.5 %)  ← LD 는 대부분 1개
- rehandle 하나하나는 확률 rh_move_prob 로 다른 bay(±rh_move_span 안)로 옮기고,
  나머지는 같은 bay 안에서 옮긴다 (제자리)
- 모든 작업에 트럭이 있다 (DS·LD = 내부 트럭, GI·GO = 외부 트럭).
  트럭 도착 a 는 크레인 순서를 따라 SC 는 sc_gap, LC 는 lc_gap 씩 늘어난다
  (0 이면 모두 0 → 처음부터 다 와 있음)

데이터 분석 참고값 (동원 2025-01, 원본 CSV)
  GO 중 rehandle 필요 51.95 %, rehandle 있는 GO 의 평균 개수 2.32,
  같은 bay 안에서 옮김 28.5 %, 다른 bay 로 옮긴 거리 중앙값 4 bay
  LD 중 rehandle 필요 3.48 % (RH_CNT 가 기록된 LD 39,445건, 2025-02-08 12:04 ~ 03-31), 있으면 평균 1.49개
  (자세한 분석: analysis/ld_rehandle/ld_rehandle_report.html)
"""

from __future__ import annotations

import random
import time

from .schema import Block, Instance, Job, Params, new_instance_id


#: 원본 CSV RH_CNT 분포 (RH_CNT > 0 인 GO 5,617건): 1~5개
RH_COUNT_DIST = {1: 1930, 2: 1526, 3: 1009, 4: 731, 5: 421}
#: LD 의 RH_CNT 분포 (RH_CNT > 0 인 LD 1,371건; 6개 1건은 5 에 합침)
RH_COUNT_DIST_LD = {1: 969, 2: 223, 3: 107, 4: 50, 5: 21}

#: "실데이터 보정" 프리셋 (2시간 분량, 초 단위). 근거는 model/model_formulation_v1.md
REAL_PRESET = {
    "n_bay": 85, "n_ds": 6, "n_ld": 4, "n_gi": 4, "n_go": 4,
    "rh_prob_go": 0.52, "rh_prob_ld": 0.035, "rh_max": 5, "rh_dist": "data",
    "rh_move_prob": 0.7, "rh_move_span": 10, "sc_gap": 584, "lc_gap": 739,
    "arrival": "even", "burst_size": 3, "go_bay": "uniform",
    "params": {"t_hat": 1.5, "pick": 75, "drop": 75, "rh_inplace": 150,
               "safety_margin": 3, "default_ready": 0},
}

#: "대기열 있는" 프리셋: 실데이터 보정값과 같고 LC(반입·반출) 트럭 간격만 739 → 200 s 로 줄임 (2026-10-13).
#: 무지원 스케줄(창 규칙 디코더, seed 0~39) GO 트럭 대기 중앙값 584/200 s 간격 13.5분 (보정값 584/739 s 는 LonRem 기준 6.2분).
#: 실제 반출 요청 → rehandle 시작 중앙값은 19분. SC 간격까지 줄이면(200/260) SC 도 늘 바빠 지원이 거의 안 생김.
QUEUE_PRESET = {**REAL_PRESET, "sc_gap": 584, "lc_gap": 200,
                "note": "반입·반출 트럭이 줄 서 있는 상황 · GO 트럭 대기 중앙값 약 13.5분 (무지원 기준)"}

#: "적하 rehandle 실험" 프리셋 (2026-10-28): 동원 데이터는 LD rehandle 이 3.5 % 로 드물어, AGV 사전 정보(LD_LOOKAHEAD)
#: 효과를 보려면 따로 만든 인스턴스가 필요. LD 6 · DS 4, LD rehandle 확률 0.5, SC 간격 400 s (SC 가 약간 밀림), LC 는 보정값.
#: 창 규칙 v2 (seed 1000~1024): 지원(도착 뒤) 4.6 % → 지원 + AGV 30분 사전 정보 15.6 % (사전 정보만, 지원 없이 5.9 %)
LD_PRESET = {**REAL_PRESET, "n_ds": 4, "n_ld": 6, "n_gi": 4, "n_go": 4, "rh_prob_ld": 0.5, "sc_gap": 400,
             "note": "적하 rehandle 실험용 (실데이터보다 LD rehandle 많음 · SC 약간 밀림) — 협업 모델·창 규칙의 'AGV 도착 H분 전부터' 옵션과 함께"}

#: "롤링 호라이즌 실험" 프리셋 (2026-10-28): 적하 rehandle 실험값을 그대로 두고 작업 수만 2배 (SC 20개 × 400 s ≈ 2.2시간)
#: → AGV 정보를 미리 아는 시간 H (0·15·30·60·120분) 를 바꿔 볼 만큼 긴 구간. experiments/run_rolling.py 의 기본값과 같음
ROLLING_PRESET = {**LD_PRESET, "n_ds": 8, "n_ld": 12, "n_gi": 8, "n_go": 8,
                  "note": "롤링 호라이즌 실험용 (적하 rehandle 실험값 × 작업 2배, 약 2~3시간) — '롤링 · …' 방법들과 함께"}


def _count(rng: random.Random, rh_max: int, dist: str, job_type: str = "GO") -> int:
    if dist == "data":
        table = RH_COUNT_DIST_LD if job_type == "LD" else RH_COUNT_DIST
        items = [(k, w) for k, w in table.items() if k <= rh_max]
        if items:
            total = sum(w for _, w in items)
            x = rng.random() * total
            for k, w in items:
                x -= w
                if x <= 0:
                    return k
            return items[-1][0]
    return rng.randint(1, rh_max)


def _rehandles(rng: random.Random, bay: int, n_bay: int, rh_prob: float, rh_max: int,
               move_prob: float, move_span: int, dist: str = "uniform",
               job_type: str = "GO") -> tuple[int, list[int] | None]:
    if rh_max <= 0 or rh_prob <= 0 or rng.random() >= rh_prob:
        return 0, None
    rc = _count(rng, rh_max, dist, job_type)
    dests, moved = [], False
    for _ in range(rc):
        if move_prob > 0 and rng.random() < move_prob:
            for _try in range(20):
                d = bay + rng.choice([-1, 1]) * rng.randint(1, max(1, move_span))
                if 1 <= d <= n_bay and d != bay:
                    dests.append(d)
                    moved = True
                    break
            else:
                dests.append(bay)
        else:
            dests.append(bay)
    return rc, (dests if moved else None)


def generate(n_bay: int = 30, n_ds: int = 3, n_ld: int = 2, n_gi: int = 2, n_go: int = 5,
             rh_prob: float = 0.6, rh_max: int = 3, rh_move_prob: float = 0.3,
             rh_move_span: int = 6, sc_gap: float = 10.0, lc_gap: float = 10.0,
             seed: int | None = None, params: Params | None = None,
             name: str | None = None, index: int = 1,
             arrival_gap: float | None = None,
             rh_prob_go: float | None = None, rh_prob_ld: float | None = None,
             rh_dist: str = "uniform", arrival: str = "even", burst_size: int = 3,
             go_bay: str = "uniform") -> Instance:
    """
    arrival : 트럭 도착 방식 (크레인 순서대로 도착, 평균 간격은 sc_gap / lc_gap 로 같음)
              "even"    일정 간격 k × gap (예전과 같음)
              "poisson" 간격이 평균 gap 인 지수분포 (무작위로 몰렸다 비었다)
              "burst"   burst_size 대씩 한꺼번에 도착, 묶음 간격 burst_size × gap
    go_bay  : GO 컨테이너가 있는 bay 분포 "uniform" | "sea" (바다 쪽에 몰림) | "land" (육지 쪽에 몰림)
              sea: bay = 1 + ⌊(N−1)u²⌋, land: bay = N − ⌊(N−1)u²⌋ (u ~ U(0,1), 중앙값이 N/4 · 3N/4 부근)
    기본값(even, uniform)이면 같은 seed 에서 예전과 똑같은 인스턴스가 나온다.
    """
    # 예전 이름(arrival_gap) 호환: LC 간격으로 쓴다
    if arrival_gap is not None:
        lc_gap = arrival_gap
    rng = random.Random(seed)
    n_bay = max(3, int(n_bay))

    def make(prefix: str, count: int) -> list[Job]:
        out = []
        for k in range(1, count + 1):
            if prefix == "GO" and go_bay in ("sea", "land"):
                off = int((n_bay - 1) * rng.random() ** 2)
                bay = 1 + off if go_bay == "sea" else n_bay - off
            else:
                bay = rng.randint(1, n_bay)
            rc, rh_d = 0, None
            if prefix in ("LD", "GO"):
                prob = rh_prob_go if prefix == "GO" else rh_prob_ld
                prob = rh_prob if prob is None else prob
                rc, rh_d = _rehandles(rng, bay, n_bay, prob, rh_max, rh_move_prob, rh_move_span, rh_dist, prefix)
            out.append(Job(f"{prefix}_{k}", prefix, bay, r_c=rc, rh_d=rh_d))
        return out

    sc = make("DS", n_ds) + make("LD", n_ld)
    lc = make("GI", n_gi) + make("GO", n_go)
    rng.shuffle(sc)
    rng.shuffle(lc)

    burst_size = max(1, int(burst_size or 1))
    for seq, gap in ((sc, sc_gap), (lc, lc_gap)):
        gap = float(gap or 0)
        t = 0.0
        for k, j in enumerate(seq):
            if arrival == "poisson":
                if k:
                    t += rng.expovariate(1.0 / gap) if gap > 0 else 0.0
                j.a = round(t, 3)
            elif arrival == "burst":
                j.a = round((k // burst_size) * burst_size * gap, 3)
            else:
                j.a = round(k * gap, 3)

    n_rh = sum(j.r_c for j in sc + lc)
    auto = (f"Rand {len(sc) + len(lc)}작업 RH{n_rh} "
            f"(bay{n_bay}, seed {seed if seed is not None else '-'})")
    nm = name or auto
    if name and index > 1:
        nm = f"{name} #{index}"
    return Instance(
        id=new_instance_id(), name=nm, block=Block(n_bay), params=params or Params(),
        seq_sc=sc, seq_lc=lc, created_at=time.time(),
        note=(f"무작위 생성: DS{n_ds} LD{n_ld} GI{n_gi} GO{n_go}, "
              f"rh_prob GO={rh_prob if rh_prob_go is None else rh_prob_go} "
              f"LD={rh_prob if rh_prob_ld is None else rh_prob_ld} ({rh_dist}), "
              f"rh_max={rh_max}, move_prob={rh_move_prob}, span={rh_move_span}, "
              f"truck gap SC={sc_gap} LC={lc_gap}"
              + (f" ({arrival}" + (f", {burst_size}대씩" if arrival == "burst" else "") + ")" if arrival != "even" else "")
              + (f", GO bay {go_bay}" if go_bay != "uniform" else "") + f", seed={seed}"),
    )


def generate_batch(count: int = 1, seed: int | None = None, **kw) -> list[Instance]:
    out = []
    for i in range(max(1, int(count))):
        s = None if seed is None else int(seed) + i
        out.append(generate(seed=s, index=i + 1, **kw))
    return out
