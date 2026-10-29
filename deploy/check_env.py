#!/usr/bin/env python3
"""
서버 환경 진단 — python deploy/check_env.py

  1. 시스템 · 파이썬   2. matplotlib   3. Gurobi (라이선스 · 크기 제한)
  4. 이 프로젝트 점검: 노트북 · 방법 목록 · 5시간 실험 세트 · 롤링 한 번 돌려 보기
"""
from __future__ import annotations

import os
import platform
import shutil
import sys
import time

OK, NO, WARN = "  [OK]  ", "  [--]  ", "  [!!]  "
problems: list[str] = []
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WEB = os.path.join(ROOT, "web")


def head(t):
    print("\n" + t); print("-" * 68)


def report(ok, label, detail="", warn=False):
    print(f"{WARN if warn else (OK if ok else NO)}{label}" + (f"  —  {detail}" if detail else ""))
    if not ok and not warn:
        problems.append(label + (f" ({detail})" if detail else ""))


def check_system():
    head("1. 시스템")
    print(f"        호스트 : {platform.node()} / {platform.platform()}")
    print(f"        파이썬 : {sys.version.split()[0]} ({sys.executable})")
    print(f"        코어   : {os.cpu_count()} 스레드 · 홈 여유 {shutil.disk_usage(os.path.expanduser('~')).free / 1024**3:.0f} GiB")
    report(sys.version_info[:2] >= (3, 9), "파이썬 3.9 이상")


def check_matplotlib():
    head("2. matplotlib (노트북 원본 차트)")
    try:
        import matplotlib; matplotlib.use("Agg")
        import matplotlib.pyplot as plt, io
        fig = plt.figure(); plt.plot([0, 1]); buf = io.BytesIO(); fig.savefig(buf, format="png"); plt.close("all")
        report(buf.tell() > 0, f"matplotlib {matplotlib.__version__}")
    except Exception as e:
        report(False, "matplotlib", str(e))


def check_gurobi():
    head("3. Gurobi")
    try:
        import gurobipy as gp
        from gurobipy import GRB
    except Exception as e:
        report(False, "gurobipy import", str(e)); return
    lic = os.environ.get("GRB_LICENSE_FILE") or os.path.expanduser("~/gurobi.lic")
    print(f"        라이선스 파일: {lic} ({'있음' if os.path.exists(lic) else '없음'})")
    try:
        env = gp.Env(empty=True); env.setParam("OutputFlag", 0); env.start()
        m = gp.Model(env=env); x = m.addVar(vtype=GRB.BINARY); m.setObjective(x, GRB.MAXIMIZE); m.optimize()
        report(m.Status == GRB.OPTIMAL, "Gurobi 소형 모델")
        m = gp.Model(env=env); xs = m.addVars(6000, vtype=GRB.BINARY); m.setObjective(xs.sum(), GRB.MAXIMIZE); m.optimize()
        report(True, "대형 모델 허용", "크기 제한 없는 라이선스 — 5시간 인스턴스 협업 MIP 가능")
    except Exception as e:
        report(False, "Gurobi 라이선스", f"크기 제한이거나 라이선스 없음: {e} (SERVER.md 3절)")


def check_app():
    head("4. 이 프로젝트")
    sys.path.insert(0, WEB)
    try:
        from app.registry import build_registry
        from app.instance_sets import load_set
        from app.methods.rules import tasks_of  # noqa: F401
    except Exception as e:
        report(False, "app 모듈 import", str(e)); return
    nb = os.path.join(ROOT, "model")
    for name in ("Reha_Crane_predetermined.ipynb", "Reha_Crane_baseline.ipynb"):
        report(os.path.exists(os.path.join(nb, name)), f"노트북 {name}")
    reg = build_registry(nb)
    for k, m in reg.items():
        ok, why = m.available()
        report(ok, f"방법 {k}", why or m.label)
    try:
        d = load_set("rolling5h", os.path.join(WEB, "data"))
        report(len(d["rows"]) == 43, f"5시간 실험 세트 {len(d['rows'])}개", d.get("source", ""))
        inst = d["instances"]["rolling5h_bal_s07_l05_1"]
        t = time.time()
        r = reg["rolling_window_v2"].solve(inst, {"objective": "truck_wait", "draw_notebook_figure": False})
        report(r.feasible, "롤링 · 창 규칙 v2 한 번 돌려 보기", f"{r.status} · 트럭 대기 {r.objective:.0f} · {time.time() - t:.1f}s")
    except Exception as e:
        report(False, "실험 세트 · 롤링", str(e))
    try:
        from app import limits
        print(f"        웹 자원 제한: 기본 {limits.DEFAULT_THREADS} 스레드 / 상한 {limits.MAX_THREADS} / 최대 {limits.MAX_TIME_LIMIT}초")
    except Exception:
        pass


def main():
    print("=" * 68); print("Rehandle 협업 — 서버 환경 진단"); print("=" * 68)
    check_system(); check_matplotlib(); check_gurobi(); check_app()
    print("\n" + "=" * 68)
    if problems:
        print(f"해결이 필요한 항목 {len(problems)}건:")
        for p in problems:
            print("  ·", p)
        return 1
    print("모든 항목 정상 — bash deploy/start.sh 로 웹을, bash deploy/run_experiment.sh 로 실험을 돌리면 됩니다.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
