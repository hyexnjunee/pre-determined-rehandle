"""
협업 모델 어댑터 — model/Reha_Crane_predetermined.ipynb
=======================================================

Predetermined sequence 는 고정하고, LD·GO 에 딸린 rehandle 을 원래 크레인이 할지
반대편 크레인이 대신 할지(x_rv)를 결정한다.
"""

from __future__ import annotations

from typing import Any

from ..schema import Instance
from .base import MethodResult, ScheduleEntry
from .notebook_model import COMMON_OPTIONS, NotebookModel


def ld_lookahead_seconds(options: dict[str, Any]):
    """웹 옵션 → LD_LOOKAHEAD (초). 끄면 None (LD 도 RH_AFTER_ARRIVAL 을 따름)."""
    if not options.get("ld_known", False):
        return None
    return max(0.0, float(options.get("ld_lookahead_min", 30) or 0) * 60.0)


def ld_lookahead_code(options: dict[str, Any]) -> str:
    v = ld_lookahead_seconds(options)
    return "None" if v is None else repr(v)


class CoopRehandle(NotebookModel):
    key = "coop"
    label = "협업 모델 (Rehandle 할당)"
    short = "협업"
    role = "proposed"
    notebook = "Reha_Crane_predetermined.ipynb"
    paper = "Predetermined Sequence + Cooperative Rehandle Assignment"
    description = ("작업 순서·할당은 고정. rehandle 을 독립 작업으로 두고 원래 크레인 / "
                   "반대편 크레인 중 누가 할지(x_rv)와 삽입 위치(y), 크레인 간 간섭 순서(z)를 결정.")
    options = {
        **COMMON_OPTIONS,
        "allow_assist": {"type": "checkbox", "default": True,
                         "label": "반대편 크레인 지원 허용 (ALLOW_ASSIST)"},
        "home_rh_fixed": {"type": "checkbox", "default": True,
                          "label": "원래 크레인 rehandle 은 본 작업 직전에만 (HOME_RH_FIXED)"},
        "rh_after_arrival": {"type": "checkbox", "default": True,
                             "label": "rehandle 은 트럭 도착 뒤에만 시작 (RH_AFTER_ARRIVAL)"},
        "ld_known": {"type": "checkbox", "default": False,
                     "label": "적하(LD) rehandle 은 AGV 도착 H분 전부터 미리 가능 (LD_LOOKAHEAD)"},
        "ld_lookahead_min": {"type": "number", "default": 30, "label": "H: AGV 도착을 미리 아는 시간 (분)"},
    }

    def switches(self, options: dict[str, Any]) -> dict[str, str]:
        return {
            "ALLOW_ASSIST": repr(bool(options.get("allow_assist", True))),
            "HOME_RH_FIXED": repr(bool(options.get("home_rh_fixed", True))),
            "RH_AFTER_ARRIVAL": repr(bool(options.get("rh_after_arrival", True))),
            "LD_LOOKAHEAD": ld_lookahead_code(options),
        }

    def extract(self, res: MethodResult, ns: dict[str, Any], inst: Instance,
                options: dict[str, Any]) -> None:
        Tasks, s, duration = ns["Tasks"], ns["s"], ns["duration"]
        Set_M, Set_R = ns["Set_M"], ns["Set_R"]
        home, parent, cand, x = ns["home"], ns["parent"], ns["cand"], ns["x"]
        for i in Set_M + Set_R:
            if i in Set_R:
                crane = next((v for v in cand[i] if x[i, v].X > 0.5), home[i])
            else:
                crane = home[i]
            st = float(s[i].X)
            du = float(duration[i])
            res.schedule.append(ScheduleEntry(
                task=i, crane=crane, o=int(Tasks[i]["o"]), d=int(Tasks[i]["d"]),
                start=round(st, 4), end=round(st + du, 4), duration=round(du, 4),
                kind="RH" if i in Set_R else "main",
                job_type=Tasks[i]["type"], parent=parent.get(i, ""), home=home[i],
                assisted=(crane != home[i]), ready=float(ns["a"].get(i, Tasks[i].get("a", 0))),
                truck_out=(round(st + float(ns["pick"]), 4) if Tasks[i]["type"] in ("DS", "GI")
                           else round(st + du, 4)),
                
            ))
        res.metrics["n_rehandles"] = len(Set_R)
        res.metrics["allow_assist"] = bool(ns.get("ALLOW_ASSIST", True))
        res.metrics["home_rh_fixed"] = bool(ns.get("HOME_RH_FIXED", True))
        res.metrics["rh_after_arrival"] = bool(ns.get("RH_AFTER_ARRIVAL", False))
        res.metrics["ld_lookahead"] = ns.get("LD_LOOKAHEAD")
        if not res.metrics["allow_assist"]:
            res.notes.append("ALLOW_ASSIST=False — 모든 rehandle 을 원래 크레인이 수행 "
                             "(비교군과 같은 값이 나와야 정상).")
        res.notes.append("RH 막대 = 독립 rehandle 작업. 반대편 크레인 행에 있으면 지원받은 것.")
        if res.metrics["ld_lookahead"] is not None:
            res.notes.append(f"LD_LOOKAHEAD={res.metrics['ld_lookahead']:.0f}s — 적하 rehandle 은 AGV 도착 "
                             f"{res.metrics['ld_lookahead'] / 60:.0f}분 전부터 가능 (반출은 RH_AFTER_ARRIVAL 그대로).")
        res.notes.append("RH_AFTER_ARRIVAL=" + str(res.metrics["rh_after_arrival"]) +
                         (" — rehandle 은 그 본 작업의 트럭 도착 뒤에만 시작." if res.metrics["rh_after_arrival"]
                          else " — rehandle 을 트럭 도착 전에 미리 할 수 있음 (a_r = 0)."))
