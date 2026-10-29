"""
비교군 어댑터 — model/Reha_Crane_baseline.ipynb
================================================

순서·할당 고정, rehandle 은 독립 작업이 아니라 LD·GO 의 처리시간에 포함.
반대편 크레인의 도움이 없는 현재 운영을 나타낸다.
"""

from __future__ import annotations

from typing import Any

from ..schema import Instance
from .base import MethodResult, ScheduleEntry
from .notebook_model import COMMON_OPTIONS, NotebookModel


class BaselineRehandle(NotebookModel):
    key = "baseline"
    label = "비교군 (Rehandle 처리시간 포함)"
    short = "비교군"
    role = "baseline"
    notebook = "Reha_Crane_baseline.ipynb"
    paper = "Predetermined Sequence, no assistance"
    description = ("작업 순서·할당 고정. rehandle 은 본 작업(LD·GO) 처리시간에 더해지고, "
                   "남는 결정은 크레인 간 간섭 순서(z)와 시작시각뿐. 현재 운영에 해당.")
    options = dict(COMMON_OPTIONS)

    def extract(self, res: MethodResult, ns: dict[str, Any], inst: Instance,
                options: dict[str, Any]) -> None:
        Tasks, s, duration = ns["Tasks"], ns["s"], ns["duration"]
        rh_time, home = ns["rehandle_time"], ns["home"]
        for i in ns["Set_T"]:
            st, du = float(s[i].X), float(duration[i])
            res.schedule.append(ScheduleEntry(
                task=i, crane=home[i], o=int(Tasks[i]["o"]), d=int(Tasks[i]["d"]),
                start=round(st, 4), end=round(st + du, 4), duration=round(du, 4),
                kind="main", job_type=Tasks[i]["type"], home=home[i],
                rh_time=float(rh_time.get(i, 0)), ready=float(Tasks[i].get("a", 0)),
                truck_out=(round(st + float(ns["pick"]), 4) if Tasks[i]["type"] in ("DS", "GI")
                           else round(st + du, 4)),
                
            ))
        res.metrics["n_rehandles"] = sum(int(Tasks[i].get("r_c", 0)) for i in ns["Set_T"])
        res.notes.append("막대 앞쪽 빗금 = 본 작업 처리시간에 포함된 rehandle 시간.")
