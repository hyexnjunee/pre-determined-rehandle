"""
롤링 호라이즌 실험 (2026-10-28)
==============================

질문: 정보가 늦게 들어오는 현실(외부트럭은 도착해야 알고, AGV 는 H분 전에 앎)에서도
      반대편 크레인의 rehandle 지원이 트럭 대기를 줄이는가? 정보를 미리 아는 시간 H 가 길수록 얼마나 좋아지는가?

설정
  - 적하(LD) rehandle 을 의도적으로 많이 둔 인스턴스 (generator.LD_PRESET: DS4 LD6 GI4 GO4, LD rehandle 확률 0.5)
  - 반출(GO) rehandle: 외부트럭 도착 뒤에만 (그 전엔 존재를 모름)
  - 적하(LD) rehandle: AGV 도착 H분 전부터 (H분 전에 정보 공개) → 누가 할지(SC/LC) 미리 결정
  - 본 작업 순서는 고정. 새 정보가 들어올 때마다(또는 --replan 30 이면 30분마다 묶어서) 작업을 투입해 다시 계획,
    다음 투입 전에 시작하는 작업만 확정

방법 (--methods)
  roll_nohelp   롤링 · 지원 없음 (rehandle 은 원래 크레인이) ← 기준
  roll_v2       롤링 · Window–Slack ver2
  roll_coop     롤링 · 협업 MIP (재계획마다 --replan-time 초)            [--mip]
  full_nohelp   정보 다 아는 정적 · 지원 없음
  full_v2       정보 다 아는 정적 · Window–Slack ver2 (같은 H 규칙)
  full_coop     정보 다 아는 정적 · 협업 MIP (같은 H 규칙) = 상한(최선)   [--mip]

지표
  gain_vs_nohelp  (롤링 무지원 − 방법) / 롤링 무지원 × 100    지원으로 줄어든 트럭 대기
  voi_vs_full     (방법 − 같은 계열 정적) / 정적 × 100        정보를 늦게 알아 잃은 몫 (roll_* ↔ full_*)
  ld_assist, go_assist   반대편 크레인이 대신 한 적하 / 반출 rehandle 수
  ld_pre         AGV 도착 전에 끝낸 적하 rehandle 수 (미리 해둔 것)
  agv_wait, et_wait      AGV(DS·LD) / 외부트럭(GI·GO) 대기 합 (초)

예시 (web 폴더에서)
  python experiments/run_rolling.py --seeds 20 --agv-info 0,15,30,60,120
  python experiments/run_rolling.py --set rolling5h --agv-info 60                      # 5시간 세트 (균형 27 + 극단 16)
  python experiments/run_rolling.py --set rolling5h --set-group 극단 --agv-info 0,60
  python experiments/run_rolling.py --set rolling5h --agv-info 60 --replan event,15,30,60   # 작업 투입 간격 비교
  python experiments/run_rolling.py --set rolling5h --mip --workers 16 --threads 8         # 연구실 서버 (128코어)
  python experiments/run_rolling.py --seeds 20 --jobs 8,12,8,8 --sc-gaps 584,400,300 --lc-gaps 739,400
  python experiments/run_rolling.py --seeds 10 --agv-info 60 --sc-gaps 584,400,300 --mip --replan-time 10
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

ALL = ["roll_nohelp", "roll_v2", "roll_coop", "full_nohelp", "full_v2", "full_coop"]
KEY = {"roll_nohelp": "rolling_nohelp", "roll_v2": "rolling_window_v2", "roll_coop": "rolling_coop",
       "full_nohelp": "window_rule_v2", "full_v2": "window_rule_v2", "full_coop": "coop"}
PAIR = {"roll_nohelp": "full_nohelp", "roll_v2": "full_v2", "roll_coop": "full_coop"}


def floats(s):
    return [float(x) for x in str(s).split(",") if x.strip()]


def strs(s):
    return [x.strip() for x in str(s).split(",") if x.strip()]


def instances(a):
    if a.set:
        from app.instance_sets import load_set
        d = load_set(a.set, os.path.join(WEB, "data"))
        for r in d["rows"]:
            if a.set_group and r["group"] != a.set_group:
                continue
            yield {"cond": f"{r['group']} · {r['label']}", "set_id": r["id"], "seed": r["seed"],
                   "rho_sc": r["rho_sc"], "rho_lc": r["rho_lc"]}, d["instances"][r["id"]]
        return
    preset = {"ld": generator.LD_PRESET, "rolling": generator.ROLLING_PRESET, "real": generator.REAL_PRESET,
              "queue": generator.QUEUE_PRESET}[a.preset]
    P = Params(**preset["params"])
    base = {k: v for k, v in preset.items() if k not in ("params", "note")}
    if a.jobs:
        base.update(zip(("n_ds", "n_ld", "n_gi", "n_go"), (int(x) for x in strs(a.jobs))))
    for arr in strs(a.arrivals):
        for sc in floats(a.sc_gaps):
            for lc in floats(a.lc_gaps):
                for seed in range(a.seeds):
                    inst = generator.generate(seed=a.seed0 + seed, params=P, **dict(
                        base, sc_gap=sc, lc_gap=lc, arrival=arr, burst_size=a.burst))
                    if inst.num_rehandles():
                        yield {"cond": f"SC {sc:g} / LC {lc:g} · {arr}", "sc_gap": sc, "lc_gap": lc,
                               "arrival": arr, "seed": a.seed0 + seed}, inst


def replan_opts(token):
    """'event' → 새 정보마다, 숫자(분) → 일정 간격."""
    t = str(token).strip()
    if t in ("", "event", "새정보", "새 정보마다"):
        return {"replan_mode": "새 정보마다"}
    return {"replan_mode": "일정 간격", "replan_interval_min": float(t)}


def replan_label(token):
    t = str(token).strip()
    return "새 정보마다" if t in ("", "event") else f"{float(t):g}분 간격"


def run(reg, name, inst, H, a, replan="event"):
    m = reg[KEY[name]]
    opt = {k: v.get("default") for k, v in (m.options or {}).items()}
    opt.update(objective=a.objective, draw_notebook_figure=False)
    if name.startswith("roll"):
        opt["agv_info_min"] = H
        opt.update(replan_opts(replan))
        opt.update(total_time_limit=a.rolling_total, plan_jobs_max=a.plan_jobs)
        if name == "roll_coop":
            opt.update(time_limit=a.replan_time, threads=a.threads, mip_gap=0.0)
    else:
        opt.update(rh_after_arrival=True, ld_known=True, ld_lookahead_min=H)
        if name == "full_nohelp":
            opt.update(help_go=False, help_ld=False, gap_search=False, recheck=False)
        if name == "full_coop":
            opt.update(time_limit=a.mip_time, threads=a.threads, mip_gap=0.0)
    t = time.time()
    with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
        r = m.solve(inst, opt)
    met = r.metrics or {}
    out = {"method": name, "status": r.status, "objective": r.objective if r.feasible else None,
           "sec": round(time.time() - t, 3), "replans": met.get("replans"),
           "delayed": met.get("delayed_in_execution"), "n_assisted": met.get("n_assisted"),
           "error": (r.error or "").strip().splitlines()[-1][:200] if r.error else ""}
    ld = go = pre = 0
    agv = et = ldw = 0.0
    for e in r.schedule or []:
        if e.kind == "RH":
            typ = next((x.job_type for x in r.schedule if x.task == e.parent), "")
            if e.assisted:
                ld += typ == "LD"
                go += typ == "GO"
            if typ == "LD":
                a_parent = next((x.ready for x in r.schedule if x.task == e.parent), 0)
                pre += e.end <= a_parent + 1e-6
        else:
            w = e.truck_out - e.ready
            if e.job_type == "LD":
                ldw += w
            if e.job_type in ("DS", "LD"):
                agv += w
            else:
                et += w
    out.update(ld_assist=ld, go_assist=go, ld_pre=pre, agv_wait=round(agv, 1), et_wait=round(et, 1), ld_wait=round(ldw, 1))
    return out


def pct(base, x):
    return None if base is None or x is None or not base else 100.0 * (base - x) / base


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--methods", default=None, help="쉼표 구분. 기본: roll_nohelp,roll_v2,full_v2 (+ --mip 이면 roll_coop,full_coop)")
    ap.add_argument("--mip", action="store_true")
    ap.add_argument("--replan", default="event",
                    help="작업 투입(재계획) 방식 목록: event = 새 정보마다, 숫자 = 그 분 간격 (예: event,15,30,60)")
    ap.add_argument("--agv-info", default="0,30,60,120", help="AGV 정보를 미리 아는 시간 H (분) 목록")
    ap.add_argument("--preset", default="ld", choices=["ld", "rolling", "real", "queue"],
                    help="ld = 적하 rehandle 실험값 (--jobs 로 개수 덮어씀), rolling = ld × 작업 2배")
    ap.add_argument("--jobs", default="8,12,8,8",
                    help="DS,LD,GI,GO 개수. 기본 8,12,8,8 (SC 20개 × 400 s ≈ 2.2시간: H 를 바꿔 볼 만큼 긴 구간). 빈칸이면 프리셋 그대로")
    ap.add_argument("--set", default="", help="실험 인스턴스 세트 (예: rolling5h = 5시간 균형 27 + 극단 16). 주면 생성 옵션은 무시")
    ap.add_argument("--set-group", default="", help="세트 중 한 묶음만: 균형 | 극단")
    ap.add_argument("--seeds", type=int, default=20)
    ap.add_argument("--seed0", type=int, default=2000)
    ap.add_argument("--sc-gaps", default="400")
    ap.add_argument("--lc-gaps", default="739")
    ap.add_argument("--arrivals", default="even")
    ap.add_argument("--burst", type=int, default=3)
    ap.add_argument("--objective", default="truck_wait", choices=["truck_wait", "makespan", "go_wait"])
    ap.add_argument("--mip-time", type=float, default=120, help="정적 협업 MIP 제한 (s)")
    ap.add_argument("--replan-time", type=float, default=10, help="롤링 협업 MIP 재계획 1회 제한 (s)")
    ap.add_argument("--rolling-total", type=float, default=1800,
                    help="롤링 1번의 전체 제한 (s, 0 = 없음). 넘으면 남은 재계획은 빠른 규칙으로 마무리")
    ap.add_argument("--plan-jobs", type=int, default=12,
                    help="재계획마다 크레인별로 앞에서 몇 개까지 계획 (0 = 아는 것 전부)")
    ap.add_argument("--threads", type=int, default=4, help="MIP 1개가 쓰는 스레드")
    ap.add_argument("--workers", type=int, default=1,
                    help="동시에 돌릴 인스턴스 수 (서버용, 예: 16). 총 코어 ≈ workers × threads 가 되게")
    ap.add_argument("--notebooks", default=default_notebook_dir(WEB))
    ap.add_argument("--out", default=os.path.join(HERE, "out", "rolling_" + datetime.now().strftime("%Y%m%d_%H%M%S")))
    a = ap.parse_args()

    methods = strs(a.methods) if a.methods else (
        ["roll_nohelp", "roll_v2", "full_nohelp", "full_v2"] + (["roll_coop", "full_coop"] if a.mip else []))
    bad = [m for m in methods if m not in ALL]
    if bad:
        sys.exit(f"알 수 없는 방법: {bad} (가능: {ALL})")
    reg = build_registry(a.notebooks)
    os.makedirs(os.path.join(a.out, "instances"), exist_ok=True)
    with open(os.path.join(a.out, "config.json"), "w", encoding="utf-8") as f:
        json.dump(vars(a), f, ensure_ascii=False, indent=1)

    items = []
    for n, (info, inst) in enumerate(instances(a), 1):
        iid = info.get("set_id") or f"roll_{n:04d}"
        with open(os.path.join(a.out, "instances", iid + ".json"), "w", encoding="utf-8") as f:
            json.dump(inst.to_dict(), f, ensure_ascii=False)
        items.append((n, iid, info, inst))

    rows, t0, done = [], time.time(), 0
    if a.workers <= 1:
        for n, iid, info, inst in items:
            rs, lines = process_instance(reg, a, methods, iid, info, inst)
            rows += [dict(r, _order=n) for r in rs]
            done += 1
            for ln in lines:
                print(f"[{time.time() - t0:7.1f}s] ({done}/{len(items)}) {ln}", flush=True)
            write_partial(a.out, rows)
    else:
        # 인스턴스 단위로 나눠 동시에 (서버용). MIP 는 프로세스마다 --threads 개 스레드
        from concurrent.futures import ProcessPoolExecutor, as_completed
        with ProcessPoolExecutor(max_workers=a.workers, initializer=_init_worker, initargs=(a.notebooks,)) as ex:
            futs = {ex.submit(_work, a, methods, iid, info, inst): n for n, iid, info, inst in items}
            for fu in as_completed(futs):
                rs, lines = fu.result()
                rows += [dict(r, _order=futs[fu]) for r in rs]
                done += 1
                for ln in lines:
                    print(f"[{time.time() - t0:7.1f}s] ({done}/{len(items)}) {ln}", flush=True)
                write_partial(a.out, rows)
    rows.sort(key=lambda r: r.pop("_order"))

    keys = []
    for r in rows:
        keys += [k for k in r if k not in keys]
    with open(os.path.join(a.out, "results.csv"), "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, keys); w.writeheader(); w.writerows(rows)
    summary = summarize(rows, methods)
    with open(os.path.join(a.out, "summary.csv"), "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, list(summary[0].keys())); w.writeheader(); w.writerows(summary)
    rep = report(summary, a)
    with open(os.path.join(a.out, "report.md"), "w", encoding="utf-8") as f:
        f.write(rep)
    print(f"\n결과: {a.out}  (results.csv · summary.csv · report.md)\n")
    print(rep)


def process_instance(reg, a, methods, iid, info, inst):
    """인스턴스 하나 × H × 투입 방식 전부. (행 목록, 진행 출력 줄)"""
    rows, lines = [], []
    n_ld = sum(j.r_c for j in inst.seq_sc if j.type == "LD")
    n_go = sum(j.r_c for j in inst.seq_lc if j.type == "GO")
    for H in floats(a.agv_info):
        static = {m: run(reg, m, inst, H, a) for m in methods if not m.startswith("roll")}
        for rp in strs(a.replan):
            res = {m: (static[m] if m in static else run(reg, m, inst, H, a, rp)) for m in methods}
            base = res.get("roll_nohelp")
            for m, r in res.items():
                full = res.get(PAIR.get(m, ""))
                rows.append({"instance": iid, **info, "H": H, "replan": replan_label(rp),
                             "jobs": inst.num_jobs(), "rh_ld": n_ld, "rh_go": n_go,
                             **r, "gain_vs_nohelp": pct(base and base["objective"], r["objective"]),
                             "voi_vs_full": (-pct(full["objective"], r["objective"])
                                             if full and full["objective"] and r["objective"] is not None else None)})
            line = " · ".join(f"{m} {res[m]['objective']:.0f}" if res[m]["objective"] is not None
                              else f"{m} {res[m]['status']}" for m in methods)
            lines.append(f"{iid} {json.dumps(info, ensure_ascii=False)} H={H:g} 투입 {replan_label(rp)} "
                         f"RH LD{n_ld}/GO{n_go} | {line}")
    return rows, lines


_REG = None


def _init_worker(notebooks):
    global _REG
    _REG = build_registry(notebooks)


def _work(a, methods, iid, info, inst):
    return process_instance(_REG, a, methods, iid, info, inst)


def write_partial(out, rows):
    """긴 실행 도중에도 끝난 인스턴스까지는 results_partial.csv 로 남긴다 (중간에 멈춰도 볼 수 있게)."""
    keys = []
    for r in rows:
        keys += [k for k in r if k not in keys and k != "_order"]
    with open(os.path.join(out, "results_partial.csv"), "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, keys, extrasaction="ignore"); w.writeheader(); w.writerows(rows)


def summarize(rows, methods):
    by = defaultdict(list)
    for r in rows:
        by[(r["cond"], r["H"], r["replan"], r["method"])].append(r)
    mean = lambda xs: round(statistics.mean(xs), 2) if xs else None
    out = []
    conds = list(dict.fromkeys(r["cond"] for r in rows))
    reps = list(dict.fromkeys(r["replan"] for r in rows))
    for (cond, H, rp, m), rs in by.items():
        ok = [r for r in rs if r["objective"] is not None]
        out.append({
            "condition": cond, "H": H, "replan": rp, "method": m, "n": len(rs), "solved": len(ok),
            "objective": mean([r["objective"] for r in ok]),
            "gain_vs_nohelp_pct": mean([r["gain_vs_nohelp"] for r in ok if r["gain_vs_nohelp"] is not None]),
            "voi_vs_full_pct": mean([r["voi_vs_full"] for r in ok if r["voi_vs_full"] is not None]),
            "ld_assist": mean([r["ld_assist"] for r in ok]), "go_assist": mean([r["go_assist"] for r in ok]),
            "ld_pre": mean([r["ld_pre"] for r in ok]),
            "rh_ld": mean([r["rh_ld"] for r in rs]), "rh_go": mean([r["rh_go"] for r in rs]),
            "agv_wait": mean([r["agv_wait"] for r in ok]), "et_wait": mean([r["et_wait"] for r in ok]),
            "ld_wait": mean([r.get("ld_wait") or 0 for r in ok]),
            "replans": mean([r["replans"] for r in ok if r["replans"] is not None]),
            "sec": mean([r["sec"] for r in rs]),
        })
    out.sort(key=lambda x: (conds.index(x["condition"]), x["H"], reps.index(x["replan"]), methods.index(x["method"])))
    return out


def report(summary, a):
    f = lambda v, d=1: "–" if v is None else f"{v:.{d}f}"
    lines = [f"# 롤링 호라이즌 실험 ({datetime.now():%Y-%m-%d %H:%M})", "",
             (f"인스턴스 세트 `{a.set}`" + (f" ({a.set_group})" if a.set_group else "") if a.set else
              f"인스턴스 `{a.preset}` 프리셋 (DS,LD,GI,GO = {a.jobs or '프리셋'})") + f" · 목적함수 `{a.objective}` · 외부트럭 정보 = 도착 시 · AGV 정보 = 도착 H분 전"
             f" · 재계획 MIP {a.replan_time:g}s · 정적 MIP {a.mip_time:g}s"
             f" · 롤링 전체 제한 {a.rolling_total:g}s · 계획 범위 크레인별 {a.plan_jobs or '전부'}", "",
             "| 조건 | H(분) | 투입 | 방법 | 풀림/n | 목적값 | 무지원 대비 % | 정보 손실 % | 적하 지원 | 반출 지원 | 미리 끝낸 적하 RH | RH(LD/GO) | AGV 대기 | 그중 적하 | ET 대기 | 재계획 | 시간 s |",
             "|---|---:|---|---|---:|---:|---:|---:|---:|---:|---:|---|---:|---:|---:|---:|---:|"]
    for s in summary:
        lines.append(f"| {s['condition']} | {s['H']:g} | {s['replan'] if s['method'].startswith('roll') else '–'} | {s['method']} | {s['solved']}/{s['n']} | {f(s['objective'], 0)} | "
                     f"{f(s['gain_vs_nohelp_pct'])} | {f(s['voi_vs_full_pct'])} | {f(s['ld_assist'])} | {f(s['go_assist'])} | "
                     f"{f(s['ld_pre'])} | {f(s['rh_ld'])}/{f(s['rh_go'])} | {f(s['agv_wait'], 0)} | {f(s['ld_wait'], 0)} | {f(s['et_wait'], 0)} | "
                     f"{f(s['replans'])} | {f(s['sec'], 2)} |")
    lines += ["", "- 무지원 대비: 롤링 · 지원 없음 대비 트럭 대기 감소율 (+ 면 좋아짐)",
              "- 정보 손실: 같은 방법을 정보 다 아는 정적 문제로 풀었을 때보다 나빠진 비율 (roll_* ↔ full_*)",
              "- 미리 끝낸 적하 RH: AGV 가 도착하기 전에 끝난 적하 rehandle 수 (H 가 클수록 늘어야 함)",
              "- 목적값 평균은 풀린 인스턴스만 (협업 MIP 가 실패한 인스턴스가 있으면 방법끼리 직접 비교하지 말고 % 지표를 볼 것)",
              "- full_* 은 '모든 도착을 처음부터 안다' 는 비현실적 기준. 단 rehandle 허용 시각(반출 = 도착 뒤, 적하 = 도착 H분 전부터)은 롤링과 같음"]
    return "\n".join(lines) + "\n"


if __name__ == "__main__":
    main()
