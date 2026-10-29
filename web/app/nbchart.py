"""
휴리스틱 결과를 노트북 원본 차트로 그리기
=========================================

MIP 노트북의 시각화 셀(9. Visualization)을 그대로 가져와, 휴리스틱 스케줄을 노트북 변수
(result_rows, makespan.X, crane_initial, delta, Set_R ...) 모양으로 넣어 실행한다.
그래서 그림이 MIP 결과 그림과 같은 규칙(안전 영역 · 빈 이동 · 대기 · rehandle 별표 · (A) 지원 표시)으로 그려진다.

- rehandle 을 독립 작업으로 둔 결과(RH 행이 있음) → 협업 노트북(Reha_Crane_predetermined.ipynb) 시각화 셀
- rehandle 을 본 작업 시간에 넣은 결과(LonRem · RemReq) → 비교군 노트북(Reha_Crane_baseline.ipynb) 시각화 셀
제목만 방법 이름으로 바꾼다.
"""

from __future__ import annotations

import json
import os
import re

from . import nbrunner as nb

_CACHE: dict[str, str] = {}
EN_TITLE = {
    "window_rule": "Heuristic: Window-Slack Assist Rule",
    "window_rule_v2": "Heuristic: Window-Slack Assist Rule ver2 (gap search + recheck)",
    "rolling_window_v2": "Rolling horizon: Window-Slack ver2 (AGV info H min ahead, ET at arrival)",
    "rolling_nohelp": "Rolling horizon: no assist",
    "rolling_coop": "Rolling horizon: cooperative MIP re-solved at each information event",
    "assist_lonrem": "Heuristic: RH Insertion + LonRem",
    "lonrem": "Heuristic: LonRem (no assist, rehandle in process time)",
    "remreq": "Heuristic: RemReq (no assist, rehandle in process time)",
}


def _chart_source(nb_path: str) -> str | None:
    key = f"{nb_path}:{os.path.getmtime(nb_path) if os.path.exists(nb_path) else 0}"
    if key in _CACHE:
        return _CACHE[key]
    if not os.path.exists(nb_path):
        return None
    src = next((c for c in nb.load_code_cells(nb_path) if "crane_colors = {" in c), None)
    if src:
        # 제목만 바꾼다 (나머지 그리는 코드는 노트북 그대로)
        src = re.sub(r'plt\.title\(f?".*?"\s*,', "plt.title(_CHART_TITLE,", src, count=1)
    _CACHE[key] = src
    return src


def draw(result, inst, notebook_dir: str, title: str | None = None) -> tuple[list[str], str]:
    """(PNG base64 목록, 안내 문구). 그릴 수 없으면 ([], 이유)."""
    ok, err = nb.matplotlib_status()
    if not ok:
        return [], f"matplotlib 을 불러올 수 없어 차트를 건너뛰었습니다: {err}"
    sched = list(result.schedule or [])
    if not sched:
        return [], ""
    split = any(e.kind == "RH" for e in sched)
    nb_name = "Reha_Crane_predetermined.ipynb" if split else "Reha_Crane_baseline.ipynb"
    src = _chart_source(os.path.join(notebook_dir, nb_name))
    if not src:
        return [], f"{nb_name} 에서 시각화 셀을 찾지 못해 차트를 건너뛰었습니다."

    from .methods.rules import init_pos
    p, n_bay = inst.params, int(inst.block.n_bay)
    dl = int(p.safety_margin) + 1
    data = {
        "rows": sorted(({"task": e.task, "crane": e.crane, "home": e.home or e.crane,
                         "assisted": bool(e.assisted), "origin": e.o, "destination": e.d,
                         "start": float(e.start), "duration": float(e.end - e.start),
                         "completion": float(e.end)} for e in sched), key=lambda r: r["start"]),
        "makespan": float(max(e.end for e in sched)),
        "init": {"SC": init_pos(inst, "SC"), "LC": init_pos(inst, "LC")},
        "t_hat": float(p.t_hat), "pick": float(p.pick), "drop": float(p.drop),
        "delta": dl, "n_bay": n_bay,
        "Set_R": [e.task for e in sched if e.kind == "RH"],
        "obj": result.objective_name or "",
        # 한글 글꼴이 없는 환경이 많아 제목은 영어로
        "title": title or f"{EN_TITLE.get(result.method, result.method)} (OBJ={result.objective_name})",
    }
    setup = f"""
import json
_d = json.loads({json.dumps(json.dumps(data, ensure_ascii=False))})
class _Val:
    def __init__(self, x): self.X = x
class _Model:
    SolCount = 1
model = _Model()
makespan = _Val(_d["makespan"])
result_rows = _d["rows"]
Set_V = ["SC", "LC"]
Set_R = _d["Set_R"]
crane_initial = _d["init"]
crane_index = {{"SC": 1, "LC": 2}}
delta = {{(v, w): _d["delta"] * abs(crane_index[v] - crane_index[w]) for v in Set_V for w in Set_V}}
t_hat, pick, drop = _d["t_hat"], _d["pick"], _d["drop"]
Lambda, h = pick, pick + drop
Bay = list(range(0, _d["n_bay"] + 2))
ALLOW_ASSIST, OBJ = True, _d["obj"]
_CHART_TITLE = _d["title"]
"""
    nb.close_figures()
    outcome = nb.execute_cells([nb.NBCell(setup, label="chart data"),
                                nb.NBCell(src, soft=True, label="notebook chart")],
                               mirror_stdout=False)
    figs = nb.capture_figures()
    note = ""
    if not outcome.ok:
        note = "차트 데이터 준비 실패: " + str(outcome.error or "")[-300:]
    elif getattr(outcome, "soft_errors", None):
        note = "차트 그리기 오류: " + str(outcome.soft_errors)[-300:]
    elif figs:
        note = f"노트북 원본 차트: {nb_name} 의 시각화 셀로 이 결과를 그림"
    return figs, note
