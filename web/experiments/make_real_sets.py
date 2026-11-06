"""
실데이터 세트 설치 (real2026_3h · real2026_5h)
=============================================

Mac 의 analysis/instance_sets_2026/out/{3h,5h}/*.json (2차 데이터에서 뽑은 인스턴스, build_instances.py)을
웹 실험 세트 폴더 web/data/instance_sets/real2026_{3h,5h}/ 로 복사하고 index.json 을 만든다.

    python experiments/make_real_sets.py            # Mac
    # 서버로: rsync -av web/data/instance_sets/real2026_3h web/data/instance_sets/real2026_5h \\
    #             hyeonjun@10.92.62.51:~/Pre-Determined_Rehandle/web/data/instance_sets/

동원 파생 데이터라 .gitignore 로 GitHub 에서 뺀다 (/web/data/instance_sets/real2026*/).
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
WEB = os.path.dirname(HERE)
ROOT = os.path.dirname(WEB)
sys.path.insert(0, WEB)

from app.instance_sets import SETS, stats   # noqa: E402
from app.schema import Instance             # noqa: E402

GROUP_ORDER = ["L1", "L2", "L3", "L4", "R"]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--src", default=os.path.join(ROOT, "analysis", "instance_sets_2026", "out"))
    ap.add_argument("--out", default=os.path.join(WEB, "data", "instance_sets"))
    a = ap.parse_args()
    for L in ("3h", "5h"):
        name = f"real2026_{L}"
        files = sorted(glob.glob(os.path.join(a.src, L, "*.json")))
        if not files:
            print(f"[!] {os.path.join(a.src, L)} 에 인스턴스가 없습니다 (analysis/instance_sets_2026/README.md)")
            continue
        folder = os.path.join(a.out, name)
        os.makedirs(folder, exist_ok=True)
        rows = []
        for p in files:
            with open(p, encoding="utf-8") as f:
                inst = Instance.from_dict(json.load(f))
            m = inst.meta or {}
            ws = m.get("window_stats_3h") or {}
            with open(os.path.join(folder, inst.id + ".json"), "w", encoding="utf-8") as f:
                json.dump(inst.to_dict(), f, ensure_ascii=False)
            st = stats(inst)
            rows.append({"id": inst.id, "key": m.get("wid"), "name": inst.name, "group": m.get("group"),
                         "label": (m.get("group_name") or "").split(" ", 1)[-1],   # "L1 목표 · …" → "목표 · …" (그룹 코드는 group 에) "desc": f"{m.get('block')} {(m.get('window') or {}).get('start')}",
                         "seed": None, "rho_target": [ws.get("sc_busy"), ws.get("lc_busy")],
                         "jobs": inst.num_jobs(), **st,
                         "actual_wait": (m.get("actual") or {}).get("truck_wait_sum"),
                         "btb_out": (m.get("btb") or {}).get("out_moves"), "spike_0709": m.get("spike_0709")})
        rows.sort(key=lambda r: (GROUP_ORDER.index(r["group"]) if r["group"] in GROUP_ORDER else 9, r["key"] or ""))
        with open(os.path.join(folder, "index.json"), "w", encoding="utf-8") as f:
            json.dump({"set": name, "title": SETS[name]["title"], "note": SETS[name]["note"], "rows": rows},
                      f, ensure_ascii=False, indent=1)
        print(f"{name}: {len(rows)}개 → {folder}")


if __name__ == "__main__":
    main()
