"""
비교 실험 일괄 실행 (2026-10-13)
================================

웹 서비스와 같은 코드(app/)로 인스턴스를 만들고 여러 방법을 돌려 표로 정리한다.
결과 폴더에 results.csv (인스턴스 × 방법 한 줄씩), summary.csv (조건별 평균),
report.md (표), instances/ (재현용 인스턴스 JSON) 를 남긴다.

묶음 (--groups, 쉼표로 여러 개)
  small  소형 검증: 본 작업 12개(DS3 LD2 GI3 GO4). 협업 MIP 가 최적까지 풀리는 크기 → 휴리스틱 RPD
  grid   부하 격자: 2시간 보정 인스턴스, SC 간격 × LC 간격 × 도착 방식
  real   실데이터 후보 구간 (analysis/instance_candidates/candidates.csv)

예시 (web 폴더에서)
  python experiments/run_experiments.py --groups small --seeds 20
  python experiments/run_experiments.py --groups grid --seeds 30 --sc-gaps 584,300 --lc-gaps 739,400,200
  python experiments/run_experiments.py --groups grid --seeds 30 --arrivals even,poisson,burst --mip
  python experiments/run_experiments.py --groups real --candidates ../analysis/instance_candidates/candidates.csv
  python experiments/run_experiments.py --groups grid --preset ld --sc-gaps 584,400,300 --lc-gaps 739 --ld-lookahead 30

방법 이름 (--methods): baseline coop window_rule window_rule_v2 window_nohelp
  window_nohelp = 창 규칙에서 지원만 끈 것 (같은 디코더의 무지원 스케줄, 지원 효과의 기준)
  grid 묶음은 2시간 인스턴스라 MIP 가 오래 걸려 기본으로 빼고, --mip 이면 baseline · coop 도 돌린다.

비교 지표
  gain_vs_nohelp  (무지원 − 방법) / 무지원       지원으로 줄어든 트럭 대기 비율
  gain_vs_base    (비교군 MIP − 방법) / 비교군    현재 운영 방식(rehandle 원래 크레인) 대비
  rpd_vs_coop     (방법 − 협업 MIP) / 협업 MIP    협업 MIP 가 OPTIMAL 일 때만
"""

from __future__ import annotations

import argparse
import contextlib
import csv
import io
import json
import os
import statistics
import sys
import time
from collections import defaultdict
from datetime import datetime

HERE = os.path.dirname(os.path.abspath(__file__))
WEB = os.path.dirname(HERE)
sys.path.insert(0, WEB)

from app import generator                       # noqa: E402
from app.schema import Params                   # noqa: E402
from app.registry import build_registry, default_notebook_dir   # noqa: E402

ALL_METHODS = ["baseline", "coop", "window_rule", "window_rule_v2", "window_nohelp"]
MIP = {"baseline", "coop"}


def floats(s):
    return [float(x) for x in str(s).split(",") if x.strip()]


def strs(s):
    return [x.strip() for x in str(s).split(",") if x.strip()]


# ---------------------------------------------------------------------- 인스턴스
def small_instances(a):
    P = Params(**generator.REAL_PRESET["params"])
    base = {k: v for k, v in generator.REAL_PRESET.items() if k != "params"}
    base.update(n_ds=3, n_ld=2, n_gi=3, n_go=4, rh_prob_ld=0.3)
    for lc in floats(a.small_lc_gaps):
        for seed in range(a.seeds):
            inst = generator.generate(seed=a.seed0 + seed, params=P, **dict(base, sc_gap=584, lc_gap=lc))
            if inst.num_rehandles():
                yield {"group": "small", "sc_gap": 584, "lc_gap": lc, "arrival": "even", "go_bay": "uniform",
                       "seed": a.seed0 + seed}, inst


def grid_instances(a):
    P = Params(**generator.REAL_PRESET["params"])
    preset = {"real": generator.REAL_PRESET, "ld": generator.LD_PRESET, "queue": generator.QUEUE_PRESET}[a.preset]
    base = {k: v for k, v in preset.items() if k not in ("params", "note")}
    for arr in strs(a.arrivals):
        for gb in strs(a.go_bays):
            for sc in floats(a.sc_gaps):
                for lc in floats(a.lc_gaps):
                    for seed in range(a.seeds):
                        inst = generator.generate(seed=a.seed0 + seed, params=P, **dict(
                            base, sc_gap=sc, lc_gap=lc, arrival=arr, burst_size=a.burst, go_bay=gb))
                        if inst.num_rehandles():
                            yield {"group": "grid", "sc_gap": sc, "lc_gap": lc, "arrival": arr, "go_bay": gb,
                                   "seed": a.seed0 + seed}, inst


def real_instances(a):
    from app.realdata import RealData, REAL_PARAMS
    rd = RealData(a.realdata, a.realcache)
    if not rd._load_index(build=True):
        sys.exit(f"원본 CSV 를 찾지 못했습니다: {a.realdata}")
    with open(a.candidates, encoding="utf-8-sig") as f:
        rows = list(csv.DictReader(f))
    for c in rows:
        inst, meta = rd.build(c["day"], c["block"], c["start"], a.minutes, dict(REAL_PARAMS), 3, None, True)
        yield {"group": "real", "subgroup": c.get("group", ""), "day": c["day"], "block": c["block"],
               "start": c["start"], "actual_wait": (meta.get("actual") or {}).get("truck_wait_sum")}, inst


# ---------------------------------------------------------------------- 실행
def run_method(reg, key, inst, a):
    opt = {"objective": a.objective, "draw_notebook_figure": False}
    if key in MIP:
        opt.update(time_limit=a.mip_time, threads=a.threads, mip_gap=0.0)
    if key in ("coop", "window_rule", "window_rule_v2", "window_nohelp"):
        opt["rh_after_arrival"] = not a.rh_before_arrival
    if a.ld_lookahead is not None and key in ("coop", "window_rule", "window_rule_v2", "window_nohelp"):
        opt.update(ld_known=True, ld_lookahead_min=a.ld_lookahead)
    if key == "window_nohelp":
        opt.update(help_go=False, help_ld=False)
    if key in ("window_rule", "window_rule_v2") and a.guard:
        opt["objective_guard"] = True
    m = reg["window_rule" if key == "window_nohelp" else key]
    t = time.time()
    with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
        r = m.solve(inst, opt)
    met = r.metrics or {}
    return {"method": key, "status": r.status, "objective": r.objective, "makespan": r.makespan,
            "best_bound": r.best_bound, "mip_gap": r.mip_gap, "sec": round(time.time() - t, 3),
            "truck_wait_sum": met.get("truck_wait_sum"), "go_wait_sum": met.get("go_wait_sum"),
            "n_rehandles": met.get("n_rehandles"), "n_assisted": met.get("n_assisted"),
            "assist_time": met.get("assist_time"), "assist_overlap": met.get("assist_overlap"),
            "assist_home_wait": met.get("assist_home_wait"),
            "stop_reasons": json.dumps(met.get("stop_reasons"), ensure_ascii=False) if met.get("stop_reasons") else "",
            "error": (r.error or "").strip().splitlines()[-1][:200] if r.error else ""}


def pct(a, b):
    return None if a is None or b is None or not b else 100.0 * (a - b) / a


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--groups", default="small,grid")
    ap.add_argument("--methods", default=None, help="쉼표 구분. 기본: 묶음별 (small·real 은 전부 MIP 포함, grid 는 휴리스틱만)")
    ap.add_argument("--mip", action="store_true", help="grid 묶음에도 baseline · coop MIP 를 돌린다")
    ap.add_argument("--seeds", type=int, default=20)
    ap.add_argument("--seed0", type=int, default=1000)
    ap.add_argument("--small-lc-gaps", default="739,260", help="소형 묶음 LC 간격 (SC 584 고정)")
    ap.add_argument("--sc-gaps", default="584,300")
    ap.add_argument("--lc-gaps", default="739,400,200")
    ap.add_argument("--arrivals", default="even", help="even,poisson,burst")
    ap.add_argument("--burst", type=int, default=3)
    ap.add_argument("--go-bays", default="uniform", help="uniform,sea,land")
    ap.add_argument("--objective", default="truck_wait", choices=["truck_wait", "makespan", "go_wait"])
    ap.add_argument("--mip-time", type=float, default=120)
    ap.add_argument("--threads", type=int, default=4)
    ap.add_argument("--rh-before-arrival", action="store_true", help="RH_AFTER_ARRIVAL 끄기 (rehandle 을 트럭 오기 전에도)")
    ap.add_argument("--ld-lookahead", type=float, default=None,
                    help="적하 rehandle 을 AGV 도착 H분 전부터 허용 (협업 MIP · 창 규칙의 LD_LOOKAHEAD). 생략하면 끔")
    ap.add_argument("--preset", default="real", choices=["real", "ld", "queue"],
                    help="grid 묶음의 기본값: real 보정값 / ld 적하 rehandle 실험값 / queue 대기열 (간격은 --sc-gaps --lc-gaps 로 덮어씀)")
    ap.add_argument("--guard", action="store_true", help="창 규칙 목적값 보호 켜기")
    ap.add_argument("--notebooks", default=default_notebook_dir(WEB))
    ap.add_argument("--realdata", default=os.path.join(WEB, "..", "data"))
    ap.add_argument("--realcache", default=os.path.join(WEB, "data", "realcache"))
    ap.add_argument("--candidates", default=os.path.join(WEB, "..", "analysis", "instance_candidates", "candidates.csv"))
    ap.add_argument("--minutes", type=float, default=60)
    ap.add_argument("--out", default=os.path.join(HERE, "out", datetime.now().strftime("%Y%m%d_%H%M%S")))
    a = ap.parse_args()

    reg = build_registry(a.notebooks)
    groups = strs(a.groups)
    os.makedirs(os.path.join(a.out, "instances"), exist_ok=True)
    with open(os.path.join(a.out, "config.json"), "w", encoding="utf-8") as f:
        json.dump(vars(a), f, ensure_ascii=False, indent=1)

    gens = {"small": small_instances, "grid": grid_instances, "real": real_instances}
    rows, t0 = [], time.time()
    for g in groups:
        if g not in gens:
            sys.exit(f"알 수 없는 묶음: {g}")
        if a.methods:
            methods = strs(a.methods)
        elif g == "grid" and not a.mip:
            methods = ["window_rule", "window_rule_v2", "window_nohelp"]
        else:
            methods = ["baseline", "coop", "window_rule", "window_rule_v2", "window_nohelp"]
        bad = [m for m in methods if m not in ALL_METHODS]
        if bad:
            sys.exit(f"알 수 없는 방법: {bad}")
        for n, (info, inst) in enumerate(gens[g](a), 1):
            iid = f"{g}_{n:04d}"
            with open(os.path.join(a.out, "instances", iid + ".json"), "w", encoding="utf-8") as f:
                json.dump(inst.to_dict(), f, ensure_ascii=False)
            res = {m: run_method(reg, m, inst, a) for m in methods}
            for m, r in res.items():
                row = {"instance": iid, **info, "jobs": inst.num_jobs(), "rh": inst.num_rehandles(), **r}
                nh, bs, cp = res.get("window_nohelp"), res.get("baseline"), res.get("coop")
                row["gain_vs_nohelp"] = pct(nh and nh["objective"], r["objective"]) if nh else None
                row["gain_vs_base"] = (pct(bs["objective"], r["objective"])
                                       if bs and bs["status"] == "OPTIMAL" else None)
                row["rpd_vs_coop"] = (-pct(cp["objective"], r["objective"])
                                      if cp and cp["status"] == "OPTIMAL" and r["objective"] is not None else None)
                rows.append(row)
            line = " · ".join(f"{m} {res[m]['objective']:.0f}" if res[m]["objective"] is not None
                              else f"{m} {res[m]['status']}" for m in methods)
            print(f"[{time.time() - t0:7.1f}s] {iid} {json.dumps(info, ensure_ascii=False)} RH {inst.num_rehandles()} | {line}",
                  flush=True)

    keys = []
    for r in rows:
        keys += [k for k in r if k not in keys]
    with open(os.path.join(a.out, "results.csv"), "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, keys); w.writeheader(); w.writerows(rows)
    summary = summarize(rows)
    with open(os.path.join(a.out, "summary.csv"), "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, list(summary[0].keys())); w.writeheader(); w.writerows(summary)
    with open(os.path.join(a.out, "report.md"), "w", encoding="utf-8") as f:
        f.write(report(summary, a))
    print(f"\n결과: {a.out}  (results.csv · summary.csv · report.md)")
    print(report(summary, a))


def cond_key(r):
    if r["group"] == "real":
        return ("real", r.get("subgroup", ""))
    return (r["group"], f"SC {r['sc_gap']:g} / LC {r['lc_gap']:g} · {r['arrival']} · GO bay {r['go_bay']}")


def summarize(rows):
    by = defaultdict(list)
    for r in rows:
        by[cond_key(r) + (r["method"],)].append(r)
    out = []
    mean = lambda xs: round(statistics.mean(xs), 2) if xs else None
    for (g, cond, m), rs in by.items():
        ok = [r for r in rs if r["objective"] is not None]
        out.append({
            "group": g, "condition": cond, "method": m, "n": len(rs),
            "solved": len(ok), "optimal": sum(1 for r in rs if r["status"] == "OPTIMAL"),
            "objective": mean([r["objective"] for r in ok]),
            "gain_vs_nohelp_pct": mean([r["gain_vs_nohelp"] for r in ok if r["gain_vs_nohelp"] is not None]),
            "gain_vs_base_pct": mean([r["gain_vs_base"] for r in ok if r["gain_vs_base"] is not None]),
            "rpd_vs_coop_pct": mean([r["rpd_vs_coop"] for r in ok if r["rpd_vs_coop"] is not None]),
            "n_rpd": sum(1 for r in ok if r["rpd_vs_coop"] is not None),
            "assisted_per_inst": mean([r["n_assisted"] or 0 for r in ok]),
            "rh_per_inst": mean([r["rh"] for r in rs]),
            "overlap_s": mean([r["assist_overlap"] or 0 for r in ok]),
            "home_wait_s": mean([r["assist_home_wait"] or 0 for r in ok]),
            "sec": mean([r["sec"] for r in rs]),
        })
    out.sort(key=lambda x: (x["group"], x["condition"], ALL_METHODS.index(x["method"])))
    return out


def report(summary, a):
    f = lambda v, d=1: "–" if v is None else f"{v:.{d}f}"
    lines = [f"# 비교 실험 결과 ({datetime.now():%Y-%m-%d %H:%M})", "",
             f"목적함수 `{a.objective}` · RH_AFTER_ARRIVAL={not a.rh_before_arrival} · LD_LOOKAHEAD={'끔' if a.ld_lookahead is None else f'{a.ld_lookahead:g}분'}"
             f" · 창 규칙 목적값 보호={a.guard} · MIP 제한 {a.mip_time:g} s", "",
             "| 묶음 | 조건 | 방법 | n | 무지원 대비 % | 비교군 MIP 대비 % | 협업 MIP 대비 RPD % (n) | 지원 RH / RH | 겹침 s | 기다림 s | 시간 s |",
             "|---|---|---|---:|---:|---:|---:|---:|---:|---:|---:|"]
    for s in summary:
        lines.append(f"| {s['group']} | {s['condition']} | {s['method']} | {s['n']} | {f(s['gain_vs_nohelp_pct'])} | "
                     f"{f(s['gain_vs_base_pct'])} | {f(s['rpd_vs_coop_pct'])} ({s['n_rpd']}) | "
                     f"{f(s['assisted_per_inst'])} / {f(s['rh_per_inst'])} | {f(s['overlap_s'], 0)} | {f(s['home_wait_s'], 0)} | {f(s['sec'], 2)} |")
    lines += ["", "- 무지원 대비: 창 규칙에서 지원만 끈 스케줄(같은 디코더) 대비 트럭 대기 감소율. + 면 좋아짐",
              "- 비교군 MIP 대비: rehandle 을 원래 크레인이 하는 현재 방식의 최적해 대비 (비교군이 OPTIMAL 인 인스턴스만)",
              "- RPD: 협업 MIP 가 OPTIMAL 인 인스턴스만. 0 이면 최적해와 같음",
              "- 겹침: 지원 rehandle 이 원래 크레인의 다른 작업과 동시에 진행된 시간, 기다림: 원래 크레인이 rehandle 끝나기를 기다린 시간"]
    return "\n".join(lines) + "\n"


if __name__ == "__main__":
    main()
