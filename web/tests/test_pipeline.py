"""
전 구간 점검: 노트북 주입 → 실행 → 해 추출 → 저장 → HTTP API

    python tests/test_pipeline.py                 # ../model 의 노트북 사용
    python tests/test_pipeline.py --notebooks DIR

실제 Gurobi 로 소형 인스턴스를 푼다 (변수 수가 적어 제한판 라이선스로도 돈다).
노트북을 고친 뒤 이걸 먼저 돌려보면 주입 지점이 깨졌는지 바로 알 수 있다.

확인하는 것
  1. 두 노트북이 모두 OPTIMAL 로 풀린다
  2. 협업 모델에서 ALLOW_ASSIST=False 로 두면 비교군과 makespan · GO 대기가 같다
  3. 협업 모델 makespan ≤ 비교군 makespan
  4. HTTP 로 실행 → 결과 저장 → 목록 · 논문 표 · CSV 가 응답한다
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import tempfile
import threading
import time
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from app.registry import build_registry, default_notebook_dir  # noqa: E402
from app.schema import default_instance, small_instance, validate  # noqa: E402
from app.server import create_server  # noqa: E402

FAILS: list[str] = []


def check(cond: bool, msg: str) -> None:
    print(("  OK   " if cond else "  FAIL ") + msg)
    if not cond:
        FAILS.append(msg)


def quiet(fn, *a, **kw):
    import io
    from contextlib import redirect_stdout, redirect_stderr
    buf = io.StringIO()
    with redirect_stdout(buf), redirect_stderr(buf):
        return fn(*a, **kw)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--notebooks", default=None)
    ap.add_argument("--realdata", default=None, help="실데이터 CSV 폴더 (기본 ../data)")
    args = ap.parse_args()
    nb_dir = args.notebooks or default_notebook_dir(ROOT)
    print("notebooks:", nb_dir)

    reg = build_registry(nb_dir)
    for k, m in reg.items():
        ok, why = m.available()
        check(ok, f"{k} 사용 가능 {why}")
    if FAILS:
        return 1

    print("\n[1] 인스턴스 검증")
    for inst in (small_instance(), default_instance()):
        v = validate(inst)
        check(not v["errors"], f"{inst.id} 검증 통과 {v['errors']}")

    print("\n[2] 노트북 실행 (소형 인스턴스)")
    inst = small_instance()
    opt = {"time_limit": 60, "threads": 2, "draw_notebook_figure": False}
    base = quiet(reg["baseline"].solve, inst, dict(opt))
    coop = quiet(reg["coop"].solve, inst, dict(opt))
    off = quiet(reg["coop"].solve, inst, dict(opt, allow_assist=False))
    for name, r in (("baseline", base), ("coop", coop), ("coop(assist off)", off)):
        check(r.status == "OPTIMAL", f"{name}: {r.status} {(r.error or '')[:200]}")
    if not FAILS:
        check(abs(off.makespan - base.makespan) < 1e-6,
              f"지원 끔 = 비교군 makespan ({off.makespan} vs {base.makespan})")
        check(abs(off.metrics['go_wait_sum'] - base.metrics['go_wait_sum']) < 1e-6,
              f"지원 끔 = 비교군 GO 대기 ({off.metrics['go_wait_sum']} vs {base.metrics['go_wait_sum']})")
        check(coop.makespan <= base.makespan + 1e-6,
              f"협업 makespan ≤ 비교군 ({coop.makespan} ≤ {base.makespan})")
        check(off.metrics["n_assisted"] == 0, "지원 끔이면 지원 rehandle 0개")
        tw = quiet(reg["coop"].solve, inst, dict(opt, objective="truck_wait"))
        tb = quiet(reg["baseline"].solve, inst, dict(opt, objective="truck_wait"))
        to = quiet(reg["coop"].solve, inst, dict(opt, objective="truck_wait", allow_assist=False))
        # makespan 목적에서는 makespan 이 같은 해가 여러 개라 트럭 대기는 트럭 대기 목적에서 비교한다
        check(abs(to.metrics["truck_wait_sum"] - tb.metrics["truck_wait_sum"]) < 1e-6,
              f"트럭 대기 목적: 지원 끔 = 비교군 ({to.metrics['truck_wait_sum']} vs {tb.metrics['truck_wait_sum']})")
        check(tw.metrics["truck_wait_sum"] <= tb.metrics["truck_wait_sum"] + 1e-6,
              f"트럭 대기 목적: 협업 ≤ 비교군 ({tw.metrics['truck_wait_sum']} ≤ {tb.metrics['truck_wait_sum']})")

    print("\n[2b] 생성기 보정")
    from app import generator
    from collections import Counter
    pre = {k: v for k, v in generator.REAL_PRESET.items() if k != "params"}
    cnt, n_go, n_go_rh, n_ld_rh, n_ld, cnt_ld = Counter(), 0, 0, 0, 0, Counter()
    for sd in range(300):
        g = generator.generate(seed=sd, **pre)
        for j in g.seq_lc:
            if j.type == "GO":
                n_go += 1
                if j.r_c:
                    n_go_rh += 1
                    cnt[j.r_c] += 1
        for j in g.seq_sc:
            if j.type == "LD":
                n_ld += 1
                if j.r_c:
                    n_ld_rh += 1
                    cnt_ld[j.r_c] += 1
    p1 = cnt[1] / max(1, sum(cnt.values()))
    check(abs(n_go_rh / n_go - 0.52) < 0.05, f"GO rehandle 비율 {n_go_rh / n_go:.3f} ≈ 0.52")
    check(abs(p1 - 0.344) < 0.05, f"rehandle 1개 비율 {p1:.3f} ≈ 0.344 (실데이터 분포)")
    check(abs(n_ld_rh / n_ld - 0.035) < 0.02, f"LD rehandle 비율 {n_ld_rh / n_ld:.3f} ≈ 0.035")
    check(not cnt_ld or cnt_ld[1] / sum(cnt_ld.values()) > 0.5, f"LD rehandle 은 대부분 1개 ({dict(cnt_ld)})")
    g0 = generator.generate(seed=1, **dict(pre, rh_prob_ld=0.0))
    check(all(j.r_c == 0 for j in g0.seq_sc), "LD rehandle 확률 0 이면 LD rehandle 없음")

    print("\n[2c] 실데이터 가져오기")
    from app.realdata import RealData
    src = args.realdata or os.path.join(ROOT, "..", "data")
    tmpc = tempfile.mkdtemp(prefix="rw_cache_")
    rd = RealData(src, tmpc)
    if not rd.status()["available"]:
        print(f"  --   원본 CSV 없음 ({src}) — 건너뜀")
    else:
        days = rd.status()["days"] or []
        idx = rd._load_index()
        day = "2025-01-06" if "2025-01-06" in idx["days"] else sorted(idx["days"])[0]
        summ = rd.day_summary(day)
        check(len(summ["blocks"]) > 0, f"{day} 블록 목록 {len(summ['blocks'])}개")
        blk = "A24" if any(b["block"] == "A24" for b in summ["blocks"]) else summ["blocks"][0]["block"]
        ri, meta = rd.build(day, blk, "09:00", 60)
        v = validate(ri)
        check(not v["errors"] and ri.num_jobs() > 0, f"{blk} {day} 09:00 1시간: 본 작업 {ri.num_jobs()}개, RH {ri.num_rehandles()} {v['errors']}")
        rb = quiet(reg["baseline"].solve, ri, dict(opt, objective="truck_wait"))
        rc = quiet(reg["coop"].solve, ri, dict(opt, objective="truck_wait"))
        check(rb.status == "OPTIMAL" and rc.status == "OPTIMAL", f"실데이터 인스턴스 풀림 ({rb.status}, {rc.status})")
        if rb.feasible and rc.feasible:
            check(rc.metrics["truck_wait_sum"] <= rb.metrics["truck_wait_sum"] + 1e-6,
                  f"협업 트럭 대기 ≤ 비교군 ({rc.metrics['truck_wait_sum']} ≤ {rb.metrics['truck_wait_sum']}), 실제 기록 {meta['actual']['truck_wait_sum']}")
        # LD rehandle: 2/8 이후는 RH_CNT 기록, 그 전은 직전 AH 로 추정, none 이면 0
        from app.realdata import LD_RH_RECORDED_FROM, rh_of
        ldd = [d for d in sorted(idx["days"]) if d >= LD_RH_RECORDED_FROM]
        early = [d for d in sorted(idx["days"]) if d < LD_RH_RECORDED_FROM and d >= "2025-01-02"]
        for dd, want in ((ldd[3] if len(ldd) > 3 else None, "record"), (early[3] if len(early) > 3 else None, "infer")):
            if not dd:
                print(f"  --   {want} 확인용 날짜 없음 — 건너뜀"); continue
            sm = rd.day_summary(dd)
            check(sm["ld_rh_source"] == want, f"{dd} LD rehandle 출처 = {sm['ld_rh_source']}")
            tot = sum(b["ld_rh"] for b in sm["blocks"])
            check(tot > 0, f"{dd} LD rehandle {tot}개 (블록 합)")
            b0 = max(sm["blocks"], key=lambda b: b["ld_rh"])
            h = max(range(24), key=lambda k: b0["hours"][k]["ld_rh"])
            ia, ma = rd.build(dd, b0["block"], f"{h:02d}:00", 60, ld_rh="auto")
            inn, mn = rd.build(dd, b0["block"], f"{h:02d}:00", 60, ld_rh="none")
            nl = sum(j.r_c for j in ia.seq_sc if j.type == "LD")
            check(nl > 0 and not validate(ia)["errors"], f"{b0['block']} {dd} {h:02d}시: LD rehandle {nl}개 ({ma['report']['ld_rh_recorded']} 기록 · {ma['report']['ld_rh_inferred']} 추정)")
            check(sum(j.r_c for j in inn.seq_sc if j.type == "LD") == 0, "ld_rh='none' 이면 LD rehandle 0")
            if want == "record":
                rb2 = quiet(reg["baseline"].solve, ia, dict(opt, objective="truck_wait"))
                rc2 = quiet(reg["coop"].solve, ia, dict(opt, objective="truck_wait"))
                check(rb2.status == "OPTIMAL" and rc2.status == "OPTIMAL", f"LD rehandle 인스턴스 풀림 ({rb2.status}, {rc2.status})")
    shutil.rmtree(tmpc, ignore_errors=True)

    print("\n[3] HTTP API")
    data = tempfile.mkdtemp(prefix="rw_test_")
    try:
        httpd = create_server("127.0.0.1", 0, ROOT, nb_dir, data)
        port = httpd.server_address[1]
        threading.Thread(target=httpd.serve_forever, daemon=True).start()
        base_url = f"http://127.0.0.1:{port}"

        def get(u):
            return json.load(urllib.request.urlopen(base_url + u))

        def post(u, b):
            req = urllib.request.Request(base_url + u, data=json.dumps(b).encode(),
                                         headers={"Content-Type": "application/json"})
            return json.load(urllib.request.urlopen(req))

        cfg = get("/api/config")
        keys = [m["key"] for m in cfg["methods"]]
        check(keys[:2] == ["baseline", "coop"], f"config 의 모델 목록 {keys}")
        rid = post("/api/run", {"instance": inst.to_dict(), "methods": ["coop", "baseline"],
                                "objective": "truck_wait", "label": "pipeline test",
                                "save_result": True, "save_instance": True,
                                "options": {"coop": opt, "baseline": opt}})["run_id"]
        t0 = time.time()
        while True:
            snap = get(f"/api/jobs/{rid}")
            if snap["status"] in ("done", "error", "cancelled") or time.time() - t0 > 300:
                break
            time.sleep(0.5)
        check(snap["status"] == "done", f"실행 완료 ({snap['status']}) {snap.get('error') or ''}")
        check([r["method"] for r in snap["results"]] == ["baseline", "coop"], "비교군이 먼저 실행")
        check(all(r["objective_name"] == "truck_wait" for r in snap["results"]), "목적함수가 두 모델에 같이 들어감")
        check(snap["saved"], "결과 저장")
        check(len(get("/api/runs")["runs"]) == 1, "결과 목록")
        check(len(get("/api/instances")["instances"]) == 1, "인스턴스 저장")
        tbl = get("/api/table")
        check(len(tbl["rows"]) == 1 and "coop" in tbl["rows"][0]["methods"], "논문 표 집계")
        csv = urllib.request.urlopen(base_url + f"/api/runs/{rid}/schedule.csv").read().decode()
        check("assisted" in csv.splitlines()[0], "스케줄 CSV")
        gen = post("/api/generate", {"seed": 1, "count": 2, "n_go": 4})
        check(len(gen["instances"]) == 2 and not validate(
            __import__("app.schema", fromlist=["Instance"]).Instance.from_dict(gen["instances"][0]))["errors"],
            "무작위 생성 + 검증")
        httpd.shutdown()
    finally:
        shutil.rmtree(data, ignore_errors=True)

    print("\n" + ("전부 통과" if not FAILS else f"실패 {len(FAILS)}건"))
    return 1 if FAILS else 0


if __name__ == "__main__":
    raise SystemExit(main())
