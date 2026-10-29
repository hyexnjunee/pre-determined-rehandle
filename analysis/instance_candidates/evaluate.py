"""비교군 · 협업 MIP · 창 규칙으로 후보 구간 평가: python evaluate.py candidates.csv <원본 CSV 폴더> <색인 캐시> <노트북 폴더> candidates_eval.csv"""
import sys, os, io, json, contextlib, time
import pandas as pd
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "web"))
from app.realdata import RealData, REAL_PARAMS
from app.registry import build_registry
cand, src, cache, nbdir, out = sys.argv[1:6]
rd = RealData(src, cache); rd._load_index(build=True)
reg = build_registry(nbdir)
rows = []
for _, c in pd.read_csv(cand).iterrows():
    inst, meta = rd.build(c.day, c.block, c.start, 60, dict(REAL_PARAMS), 3, None, True)
    row = {"group": c.group, "day": c.day, "block": c.block, "start": c.start,
           "jobs": inst.num_jobs(), "rh": inst.num_rehandles(), "actual_wait": meta["actual"].get("truck_wait_sum")}
    o = {"objective": "truck_wait", "time_limit": 120, "draw_notebook_figure": False, "threads": 4}
    for key, opt in (("baseline", o), ("coop", dict(o, rh_after_arrival=True)), ("window_rule", o),
                     ("window_nohelp", dict(o, help_go=False, help_ld=False))):
        t = time.time()
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            r = reg["window_rule" if key == "window_nohelp" else key].solve(inst, opt)
        m = r.metrics
        row.update({f"{key}": r.objective, f"{key}_status": r.status, f"{key}_sec": round(time.time() - t, 2),
                    f"{key}_assist": m.get("n_assisted"), f"{key}_overlap": m.get("assist_overlap"),
                    f"{key}_wait": m.get("assist_home_wait")})
        if key == "window_rule":
            row["window_stop"] = json.dumps(m.get("stop_reasons"), ensure_ascii=False)
    rows.append(row); print(json.dumps(row, ensure_ascii=False), flush=True)
pd.DataFrame(rows).to_csv(out, index=False, encoding="utf-8-sig")
