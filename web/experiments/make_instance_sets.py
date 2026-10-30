"""
실험 인스턴스 세트 파일 만들기 (2026-10-28)
==========================================

app/instance_sets.py 의 정의(시드 고정)로 인스턴스를 만들어 web/data/instance_sets/<세트>/ 에 저장한다.
  index.json        세트 설명 + 인스턴스별 요약 (묶음, 작업 수, rehandle 수, 실제 부하 ρ)
  <id>.json         인스턴스 (웹 인스턴스 형식 그대로)
웹 실행 탭 → "실험 세트" 와 experiments/run_rolling.py --set rolling5h 가 이 폴더를 읽는다.
폴더가 없으면 서버가 같은 정의로 바로 만들어 쓰므로, 이 스크립트는 파일로 남겨 두고 싶을 때만 돌리면 된다.

  python experiments/make_instance_sets.py              # 전부 (rolling5h · rolling10h)
  python experiments/make_instance_sets.py --set rolling10h
"""

from __future__ import annotations

import argparse
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
WEB = os.path.dirname(HERE)
sys.path.insert(0, WEB)

from app.instance_sets import SETS, build_set, stats   # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--set", default="all", choices=sorted(SETS) + ["all"], help="세트 이름 (기본 all = 전부)")
    ap.add_argument("--out", default=os.path.join(WEB, "data", "instance_sets"))
    a = ap.parse_args()
    for name in (sorted(SETS) if a.set == "all" else [a.set]):
        make(name, a.out)


def make(set_name, out):
    folder = os.path.join(out, set_name)
    os.makedirs(folder, exist_ok=True)
    rows = []
    for spec, inst in build_set(set_name):
        with open(os.path.join(folder, inst.id + ".json"), "w", encoding="utf-8") as f:
            json.dump(inst.to_dict(), f, ensure_ascii=False)
        rows.append({"id": inst.id, "key": spec["key"], "name": inst.name, "group": spec["group"],
                     "label": spec["label"], "desc": spec["desc"], "seed": spec["seed"],
                     "rho_target": [spec["rho_sc"], spec["rho_lc"]], "jobs": inst.num_jobs(), **stats(inst)})
        print(f"{inst.name:34s} 작업 {inst.num_jobs():3d}  RH LD {rows[-1]['rh_ld']:3d} GO {rows[-1]['rh_go']:3d}  "
              f"ρ {rows[-1]['rho_sc']:.2f} / {rows[-1]['rho_lc']:.2f}  가장 바쁜 1시간 {rows[-1]['rho_peak_sc']:.2f} / {rows[-1]['rho_peak_lc']:.2f}")
    with open(os.path.join(folder, "index.json"), "w", encoding="utf-8") as f:
        json.dump({"set": set_name, "title": SETS[set_name]["title"], "note": SETS[set_name]["note"], "rows": rows},
                  f, ensure_ascii=False, indent=1)
    print(f"\n{len(rows)}개 → {folder}")


if __name__ == "__main__":
    main()
