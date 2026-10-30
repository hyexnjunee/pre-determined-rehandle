"""
실험용 인스턴스 세트 (2026-10-28)
================================

롤링 호라이즌 실험용 "5시간" 세트. 웹 실행 탭의 "실험 세트" 와 experiments/run_rolling.py --set 에서 쓴다.
같은 정의(시드 고정)로 언제 만들어도 똑같은 인스턴스가 나온다. 만든 결과는 web/data/instance_sets/<세트>/ 에 저장
(experiments/make_instance_sets.py).

공통
  - 시간 창 T = 5시간 (18,000 s): 크레인별 트럭 도착을 [0, T) 안에 고르게 (작업 수 = ⌊T / 간격⌋)
  - 파라미터·rehandle 분포는 "적하 rehandle 실험값" (LD rehandle 확률 0.5 · GO 0.52 실데이터 분포 · 85 bay)
  - SC = DS 절반 + LD 절반, LC = GI 절반 + GO 절반

부하 ρ (작업량 / 5시간): 본 작업 + 원래 크레인 rehandle + 작업 사이 빈 이동 시간의 합 ÷ T.
  SC 작업 1개 ≈ 310 s, LC 작업 1개 ≈ 380 s (rehandle 포함 평균) → 간격 = 작업시간 / ρ

균형 세트 (bal): SC ρ × LC ρ = {0.5, 0.7, 0.9}² 9칸 × 시드 3개 = 27개 → 부하 공간을 고르게 덮음
극단 세트 (ext): 8가지 × 시드 2개 = 16개
시드는 실제 부하가 목표 ± 0.04 안에 드는 것만 골라 쓴다 (rehandle 개수가 많이 나온 시드는 부하가 튀므로). rehandle 폭증 2가지는 예외

"10시간" 세트 rolling10h (2026-10-30) — 시간 창 T = 10시간 (36,000 s)
  균형 18개   SC ρ × LC ρ = {0.5, 0.7, 0.9}² × 시드 2 (도착 고르게)
  대규모 6개  작업이 아주 많음: 두 크레인 1.0 (≈210작업) · 과부하 1.15 (≈240작업) · rehandle 이 드물어 작업이 짧고 아주 많은 1.0 (≈260작업) × 시드 2
  몰림 20개   평균 부하는 SC 0.7 · LC 0.7 로 같고 도착이 특정 시간대에 몰림 (10가지 × 시드 2):
              오전 · 한낮 · 저녁 몰림, 두 번 몰림, 1시간 폭주, SC만 · LC만 몰림, 엇갈린 몰림, 동시 몰림, 점점 증가
  도착 시각: 30분 칸마다 상대 도착률 w 를 주고, 누적 도착률의 (k + u)/n 분위점에 k번째 트럭 (u ~ U(0.2, 0.8))
  → 순서는 그대로, 몰린 칸에는 간격이 좁아짐. 표의 '가장 바쁜 1시간 부하' 로 몰림 정도를 본다
"""

from __future__ import annotations

import copy

from .generator import LD_PRESET, generate
from .schema import Instance, Params

T5H = 18000.0
T10H = 36000.0
SC_JOB, LC_JOB = 310.0, 380.0          # 작업 1개 평균 작업량 (s), 롤링 실험값 기준으로 잰 값
LEVELS = (0.5, 0.7, 0.9)

EXTREMES = [
    ("sc_over", "SC 과부하 · LC 한가", "해측(SC)만 넘치게 바쁨 — LC 가 적하 rehandle 을 도울 여유가 가장 큰 경우",
     dict(rho_sc=1.05, rho_lc=0.3)),
    ("lc_over", "LC 과부하 · SC 한가", "육측(LC)만 넘치게 바쁨 — SC 가 반출 rehandle 을 도울 여유가 가장 큰 경우",
     dict(rho_sc=0.3, rho_lc=1.05)),
    ("both_over", "둘 다 과부하", "두 크레인 모두 일이 넘침 — 도울 틈이 거의 없는 대조군",
     dict(rho_sc=1.05, rho_lc=1.05)),
    ("both_idle", "둘 다 한가", "두 크레인 모두 여유 — 대기 자체가 적어 지원 효과가 작아야 하는 대조군",
     dict(rho_sc=0.3, rho_lc=0.3)),
    ("ld_heavy", "적하 rehandle 폭증", "적하(LD)의 90 %에 rehandle 1~5개 (균등) — AGV 사전 정보·적하 지원이 가장 중요한 경우",
     dict(rho_sc=0.7, rho_lc=0.7, rh_prob_ld=0.9, rh_dist="uniform")),
    ("go_heavy", "반출 rehandle 폭증", "반출(GO)의 90 %에 rehandle 1~5개 (균등) — 도착해야 아는 rehandle 이 몰린 경우",
     dict(rho_sc=0.7, rho_lc=0.7, rh_prob_go=0.9, rh_dist="uniform")),
    ("burst", "트럭 몰려서 도착", "6대씩 한꺼번에 도착 (평균 간격은 같음) — 순간 대기열",
     dict(rho_sc=0.7, rho_lc=0.7, arrival="burst", burst_size=6)),
    ("go_sea", "반출이 해측에 몰림", "GO 컨테이너가 바다 쪽 bay 에 몰림 — 두 크레인 간섭(ETD)이 잦은 경우",
     dict(rho_sc=0.7, rho_lc=0.7, go_bay="sea")),
]


TOL = 0.04          # 부하를 맞추는 세트: 실제 ρ 가 목표 ± 0.04 안인 시드만 쓴다 (rehandle 개수 운에 따른 흔들림 제거)


def _spec(key, group, label, desc, seed, rho_sc, rho_lc, match=True, set_name="rolling5h",
          profile_sc=None, profile_lc=None, job_sc=None, job_lc=None, **gen):
    sp = {"key": key, "group": group, "label": label, "desc": desc, "seed": seed,
          "rho_sc": rho_sc, "rho_lc": rho_lc, "match": match, "gen": gen, "set": set_name}
    if profile_sc is not None:
        sp["profile_sc"], sp["profile_lc"] = profile_sc, profile_lc
    if job_sc is not None:
        sp["job_sc"], sp["job_lc"] = job_sc, job_lc
    return sp


def _pick_seeds(proto: dict, start: int, count: int) -> list[int]:
    """start 부터 시드를 올려 가며, 실제 부하가 목표 ± TOL 안인 것 count 개 (match=False 면 그냥 연속)."""
    if not proto["match"]:
        return list(range(start, start + count))
    out, seed = [], start
    while len(out) < count and seed < start + 500:
        st = stats(build(dict(proto, seed=seed), proto.get("set", "rolling5h")))
        if abs(st["rho_sc"] - proto["rho_sc"]) <= TOL and abs(st["rho_lc"] - proto["rho_lc"]) <= TOL:
            out.append(seed)
        seed += 1
    return out


_CACHE: dict[str, list[dict]] = {}


def specs_rolling5h() -> list[dict]:
    if "rolling5h" in _CACHE:
        return _CACHE["rolling5h"]
    out = []
    for i, rs in enumerate(LEVELS):
        for j, rl in enumerate(LEVELS):
            proto = _spec("", "균형", f"SC {rs:.1f} · LC {rl:.1f}", "부하 격자 (작업량 / 5시간)", 0, rs, rl)
            for k, seed in enumerate(_pick_seeds(proto, 50000 + 1000 * (3 * i + j), 3)):
                out.append(dict(proto, key=f"bal_s{int(rs * 10):02d}_l{int(rl * 10):02d}_{k + 1}", seed=seed))
    for n, (key, label, desc, kw) in enumerate(EXTREMES):
        kw = dict(kw)
        rs, rl = kw.pop("rho_sc"), kw.pop("rho_lc")
        # rehandle 폭증은 부하가 목표보다 커지는 것이 요점이라 맞추지 않음
        proto = _spec("", "극단", label, desc, 0, rs, rl, match=not key.endswith("_heavy"), **kw)
        for k, seed in enumerate(_pick_seeds(proto, 60000 + 1000 * n, 2)):
            out.append(dict(proto, key=f"ext_{key}_{k + 1}", seed=seed))
    _CACHE["rolling5h"] = out
    return out


# ---------------------------------------------------------------------- 10시간 세트
BINS = 20                                   # 30분 칸 20개 = 10시간


def _bins(*segs, base=1.0):
    """(시작 시, 끝 시, 배수) 구간들로 30분 칸 20개의 상대 도착률."""
    w = [base] * BINS
    for h0, h1, m in segs:
        for b in range(BINS):
            mid = (b + 0.5) / 2
            if h0 <= mid < h1:
                w[b] = m
    return w


def _bump(center_h, width_h, peak, base=1.0):
    import math
    return [base + (peak - base) * math.exp(-0.5 * (((b + 0.5) / 2 - center_h) / (width_h / 2.355)) ** 2)
            for b in range(BINS)]


FLAT = [1.0] * BINS
PEAKS = [
    ("morning", "오전 몰림", "처음 3시간에 도착이 2.5배 — 일과 시작 직후 대기열", _bins((0, 3, 2.5), base=0.55), None),
    ("noon", "한낮 몰림", "5시간째를 중심으로 폭 2시간 봉우리 (최대 3배)", _bump(5, 2, 3.0, base=0.6), None),
    ("evening", "저녁 몰림", "마지막 3시간에 2.5배 — 마감 직전 몰림", _bins((7, 10, 2.5), base=0.55), None),
    ("twin", "두 번 몰림", "2.5시간 · 7.5시간에 봉우리 두 개 (최대 2.5배)",
     [a + b - 0.6 for a, b in zip(_bump(2.5, 1.6, 2.5, 0.3), _bump(7.5, 1.6, 2.5, 0.3))], None),
    ("surge", "1시간 폭주", "3~4시간째 1시간만 4배 — 갑작스러운 폭주", _bins((3, 4, 4.0), base=0.8), None),
    ("sc_only", "SC만 몰림", "해측(AGV)만 3~6시간째 2.5배, 육측은 고르게 — LC 가 도울 여유", _bins((3, 6, 2.5), base=0.55), FLAT),
    ("lc_only", "LC만 몰림", "육측(외부트럭)만 3~6시간째 2.5배, 해측은 고르게 — SC 가 도울 여유", FLAT, _bins((3, 6, 2.5), base=0.55)),
    ("stagger", "엇갈린 몰림", "SC 는 2~4시간째, LC 는 6~8시간째 2.5배 — 돕는 쪽이 번갈아 바뀜",
     _bins((2, 4, 2.5), base=0.7), _bins((6, 8, 2.5), base=0.7)),
    ("together", "동시 몰림", "SC · LC 모두 4~6시간째 2.5배 — 몰릴 때 도울 틈이 없음", _bins((4, 6, 2.5), base=0.7), None),
    ("ramp", "점점 증가", "도착률이 처음 0.3배에서 끝 1.7배로 점점 늘어남", [0.3 + 1.4 * (b + 0.5) / BINS for b in range(BINS)], None),
]


def specs_rolling10h() -> list[dict]:
    if "rolling10h" in _CACHE:
        return _CACHE["rolling10h"]
    out = []
    for i, rs in enumerate(LEVELS):
        for j, rl in enumerate(LEVELS):
            proto = _spec("", "균형", f"SC {rs:.1f} · LC {rl:.1f}", "부하 격자 (작업량 / 10시간), 도착 고르게", 0, rs, rl,
                          set_name="rolling10h")
            for k, seed in enumerate(_pick_seeds(proto, 70000 + 1000 * (3 * i + j), 2)):
                out.append(dict(proto, key=f"bal_s{int(rs * 10):02d}_l{int(rl * 10):02d}_{k + 1}", seed=seed))
    bigs = [("big10", "작업 많음 · 1.0", "두 크레인 모두 부하 1.0 (꽉 참) — 10시간에 작업 약 210개", 1.0, 1.0, {}),
            ("big115", "작업 많음 · 과부하 1.15", "두 크레인 모두 부하 1.15 — 대기열이 계속 쌓임, 작업 약 240개", 1.15, 1.15, {}),
            ("bigshort", "작업 아주 많음 · 짧은 작업",
             "rehandle 이 드물어(LD 5 % · GO 10 %) 작업 하나가 짧은 대신 개수가 아주 많음 — 부하 1.0 · 작업 약 260개",
             1.0, 1.0, {"rh_prob_ld": 0.05, "rh_prob_go": 0.1, "job_sc": 263.0, "job_lc": 280.0})]
    for n, (key, label, desc, rs, rl, kw) in enumerate(bigs):
        proto = _spec("", "대규모", label, desc, 0, rs, rl, set_name="rolling10h", **kw)
        for k, seed in enumerate(_pick_seeds(proto, 80000 + 1000 * n, 2)):
            out.append(dict(proto, key=f"{key}_{k + 1}", seed=seed))
    for n, (key, label, desc, wsc, wlc) in enumerate(PEAKS):
        proto = _spec("", "몰림", label, desc, 0, 0.7, 0.7, set_name="rolling10h",
                      profile_sc=list(wsc), profile_lc=list(wlc if wlc is not None else wsc))
        for k, seed in enumerate(_pick_seeds(proto, 90000 + 1000 * n, 2)):
            out.append(dict(proto, key=f"peak_{key}_{k + 1}", seed=seed))
    _CACHE["rolling10h"] = out
    return out


def _profile_arrivals(n: int, weights: list[float], T: float, rng) -> list[float]:
    """상대 도착률(칸별)의 누적 분포에서 (k + u)/n 분위점 → n 개 도착 시각 (오름차순)."""
    import bisect
    nb = len(weights)
    width = T / nb
    cum = [0.0]
    for w in weights:
        cum.append(cum[-1] + max(1e-9, float(w)) * width)
    tot = cum[-1]
    out = []
    for k in range(n):
        q = (k + rng.uniform(0.2, 0.8)) / n * tot
        b = min(nb - 1, bisect.bisect_right(cum, q) - 1)
        out.append(round(b * width + (q - cum[b]) / (max(1e-9, float(weights[b]))), 3))
    return out


SETS = {
    "rolling5h": {
        "title": "롤링 실험 세트 (5시간)",
        "note": "트럭 도착이 5시간에 고르게 퍼진 인스턴스. 균형 27개(SC·LC 부하 0.5/0.7/0.9 격자 × 시드 3) + 극단 16개(8가지 × 시드 2). "
                "적하 rehandle 실험값 기반 (LD rehandle 50 %). '롤링 · …' 방법과 함께.",
        "specs": specs_rolling5h,
        "T": T5H, "tag": "5h",
    },
    "rolling10h": {
        "title": "롤링 실험 세트 (10시간)",
        "note": "10시간 인스턴스. 균형 18개(SC·LC 부하 0.5/0.7/0.9 × 시드 2, 도착 고르게) + 대규모 6개(작업 210~260개) "
                "+ 몰림 20개(평균 부하 0.7 로 같고 도착이 특정 시간대에 몰림, 10가지 × 시드 2). 적하 rehandle 실험값 기반.",
        "specs": specs_rolling10h,
        "T": T10H, "tag": "10h",
    },
}


def build(spec: dict, set_name: str | None = None) -> Instance:
    """정의 하나 → 인스턴스 (시드 고정이라 항상 같음)."""
    import random
    set_name = set_name or spec.get("set", "rolling5h")
    T = SETS[set_name]["T"]
    params = Params(**LD_PRESET["params"])
    kw = {k: v for k, v in LD_PRESET.items() if k not in ("params", "note")}
    gen = dict(spec["gen"])
    sc_gap = spec.get("job_sc", SC_JOB) / spec["rho_sc"]
    lc_gap = spec.get("job_lc", LC_JOB) / spec["rho_lc"]
    n_sc, n_lc = int(T // sc_gap), int(T // lc_gap)
    kw.update(n_ds=n_sc // 2, n_ld=n_sc - n_sc // 2, n_gi=n_lc // 2, n_go=n_lc - n_lc // 2,
              sc_gap=round(sc_gap, 1), lc_gap=round(lc_gap, 1), **gen)
    inst = generate(seed=spec["seed"], params=params, **kw)
    if "profile_sc" in spec:
        # 시간대별 도착률 → 크레인 순서대로 도착 시각 다시 매김
        rng = random.Random(spec["seed"] * 7 + 3)
        for seq, w in ((inst.seq_sc, spec["profile_sc"]), (inst.seq_lc, spec["profile_lc"])):
            for j, a in zip(seq, _profile_arrivals(len(seq), w, T, rng)):
                j.a = a
    # 포아송 등으로 시간 창을 넘으면 그 크레인 도착 시각을 [0, T − 간격] 으로 줄인다
    for seq, gap in ((inst.seq_sc, sc_gap), (inst.seq_lc, lc_gap)):
        last = max((j.a or 0) for j in seq) if seq else 0
        lim = T - min(gap, 60.0) if "profile_sc" in spec else T - gap
        if last > lim:
            for j in seq:
                j.a = round((j.a or 0) * lim / last, 3)
    tag = f"{SETS[set_name]['tag']} {spec['group']}"
    num = spec["key"].rsplit("_", 1)[-1]
    inst.id = f"{set_name}_{spec['key']}"
    inst.name = f"[{tag}] {spec['label']} #{num}"
    inst.created_at = 0.0
    inst.note = (f"{SETS[set_name]['title']} · {spec['group']} · {spec['label']} — {spec['desc']}. "
                 f"목표 부하 SC {spec['rho_sc']} / LC {spec['rho_lc']} ({'평균 ' if 'profile_sc' in spec else ''}간격 {sc_gap:.0f} / {lc_gap:.0f} s), "
                 f"seed {spec['seed']}" + (f", {spec['gen']}" if spec["gen"] else ""))
    inst.meta = {"set": set_name, "key": spec["key"], "group": spec["group"], "label": spec["label"],
                 "desc": spec["desc"], "seed": spec["seed"], "rho_target": [spec["rho_sc"], spec["rho_lc"]],
                 "time_window": T}
    return inst


def stats(inst: Instance) -> dict:
    """표에 보일 값: 작업 수, rehandle 수, 평균 부하 ρ (작업량 / 시간 창), 가장 바쁜 1시간 부하, 시간별 도착 수, 마지막 도착."""
    from .methods.rules import tasks_of
    p = inst.params
    T = float((inst.meta or {}).get("time_window") or T5H)
    tasks, seqs, _ = tasks_of(inst, False)
    out = {}
    nh = max(1, int(round(T / 3600)))
    for c in ("SC", "LC"):
        seq = seqs[c]
        work = {t: tasks[t].duration for t in seq}
        for a, b in zip(seq, seq[1:]):
            work[b] += abs(tasks[a].d - tasks[b].o) * p.t_hat
        out["rho_" + c.lower()] = round(sum(work.values()) / T, 3)
        # 도착 시각 기준, 1시간 창(15분씩 밀며)에 도착한 작업의 작업량 / 3600 의 최댓값
        arr = sorted((tasks[t].ready, work[t]) for t in seq)
        best, t0 = 0.0, 0.0
        while t0 < T - 1e-9:
            best = max(best, sum(w for a, w in arr if t0 <= a < t0 + 3600) / 3600)
            t0 += 900
        out["rho_peak_" + c.lower()] = round(best, 2)
        hist = [0] * nh
        for a, _w in arr:
            hist[min(nh - 1, int(a // 3600))] += 1
        out["hist_" + c.lower()] = hist
    cnt = inst.counts()
    return {
        "n_ds": cnt.get("DS", 0), "n_ld": cnt.get("LD", 0), "n_gi": cnt.get("GI", 0), "n_go": cnt.get("GO", 0),
        "rh_ld": sum(j.r_c for j in inst.seq_sc if j.type == "LD"),
        "rh_go": sum(j.r_c for j in inst.seq_lc if j.type == "GO"),
        **out,
        "hours": round(T / 3600, 1),
        "last_arrival_h": round(max(inst.ready(j) for j in inst.jobs()) / 3600, 2),
    }


def build_set(set_name: str = "rolling5h") -> list[tuple[dict, Instance]]:
    """세트 전체 (정의 순서대로)."""
    return [(s, build(s, set_name)) for s in SETS[set_name]["specs"]()]


def copy_for_storage(inst: Instance) -> Instance:
    return copy.deepcopy(inst)


def load_set(set_name: str, data_dir: str | None = None) -> dict:
    """세트 목록 + 인스턴스. data_dir/instance_sets/<세트>/index.json 이 있으면 그 파일, 없으면 정의로 바로 만든다."""
    import json
    import os
    from .schema import Instance as _I
    if set_name not in SETS:
        raise KeyError(set_name)
    folder = os.path.join(data_dir, "instance_sets", set_name) if data_dir else ""
    idx = os.path.join(folder, "index.json") if folder else ""
    if idx and os.path.exists(idx):
        with open(idx, encoding="utf-8") as f:
            d = json.load(f)
        insts = {}
        for r in d["rows"]:
            with open(os.path.join(folder, r["id"] + ".json"), encoding="utf-8") as f:
                insts[r["id"]] = _I.from_dict(json.load(f))
        return {**d, "source": folder, "instances": insts}
    rows, insts = [], {}
    for spec, inst in build_set(set_name):
        insts[inst.id] = inst
        rows.append({"id": inst.id, "key": spec["key"], "name": inst.name, "group": spec["group"],
                     "label": spec["label"], "desc": spec["desc"], "seed": spec["seed"],
                     "rho_target": [spec["rho_sc"], spec["rho_lc"]], "jobs": inst.num_jobs(), **stats(inst)})
    return {"set": set_name, "title": SETS[set_name]["title"], "note": SETS[set_name]["note"],
            "rows": rows, "source": "정의에서 바로 생성", "instances": insts}
