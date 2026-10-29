"""실데이터 1시간 구간 스캔: 반출 대기 · GO rehandle · SC 여유 (2026-10-13).

사용: python scan.py <원본 CSV 폴더> <색인 캐시 폴더> <출력 폴더>
web/app/realdata.py 의 색인과 rehandle 복원(rh_of)을 그대로 쓴다.
"""
import sys, os, csv, json, calendar, statistics as st
from datetime import datetime
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "web"))
from app.realdata import RealData, rh_of, MAIN

src, cache, out = sys.argv[1:4]
rd = RealData(src, cache); idx = rd._load_index(build=True)

def busy(rows, t0, t1):
    iv = sorted((max(r[2], t0), min(r[3] or r[2], t1)) for r in rows if r[2] < t1 and (r[3] or r[2]) > t0)
    tot, cur = 0.0, None
    for a, b in iv:
        if b <= a: continue
        if cur is None or a > cur[1]:
            if cur: tot += cur[1] - cur[0]
            cur = [a, b]
        else:
            cur[1] = max(cur[1], b)
    if cur: tot += cur[1] - cur[0]
    return tot / (t1 - t0)

recs = []
for day in sorted(idx["days"]):
    d0 = calendar.timegm(datetime.strptime(day, "%Y-%m-%d").timetuple())
    for blk, (sc, lc) in sorted(idx["pairs"].items()):
        rows = rd._rows_around(day, (sc, lc))
        nb = int(idx["max_bay"].get(blk, 85))
        rs_sc, rs_lc = rows[sc], rows[lc]
        if not any(d0 <= r[2] < d0 + 86400 for r in rs_lc):
            continue
        for h in range(24):
            t0, t1 = d0 + 3600 * h, d0 + 3600 * (h + 1)
            go = [(k, r) for k, r in enumerate(rs_lc) if r[0] == "GO" and t0 <= r[2] < t1]
            gi = sum(1 for r in rs_lc if r[0] == "GI" and t0 <= r[2] < t1)
            scm = [r for r in rs_sc if r[0] in ("DS", "LD") and t0 <= r[2] < t1]
            if not go and not gi and not scm:
                continue
            go_rh = sum(rh_of(rs_lc, k, nb, "auto")[0] for k, _ in go)
            waits = [(r[3] - r[4]) / 60 for _, r in go if r[3] and r[4]]
            queued = sum(1 for _, r in go if r[4] and r[4] < t0)
            recs.append({
                "day": day, "block": blk, "start": f"{h:02d}:00", "sc": sc, "lc": lc,
                "n_go": len(go), "n_gi": gi, "n_sc_main": len(scm), "go_rh": go_rh,
                "go_rh_jobs": sum(1 for k, _ in go if rh_of(rs_lc, k, nb, "auto")[0]),
                "go_wait_med_min": round(st.median(waits), 1) if waits else None,
                "go_queued_at_start": queued,
                "sc_busy": round(busy(rs_sc, t0, t1), 3), "lc_busy": round(busy(rs_lc, t0, t1), 3),
            })
os.makedirs(out, exist_ok=True)
keys = list(recs[0].keys())
with open(os.path.join(out, "hour_windows_all.csv"), "w", newline="", encoding="utf-8-sig") as f:
    w = csv.DictWriter(f, keys); w.writeheader(); w.writerows(recs)
print(len(recs), "windows")
