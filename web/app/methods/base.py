"""
방법론 어댑터 공통 인터페이스
=============================

어댑터가 지켜야 할 것은 하나 — `solve()` 가 `MethodResult` 를 돌려주는 것.
그러면 비교표·개선율·Gantt·저장·논문 표가 전부 자동으로 붙는다.
"""

from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import Any

from ..schema import Instance


@dataclass
class ScheduleEntry:
    task: str
    crane: str                 # SC | LC
    o: int
    d: int
    start: float
    end: float
    kind: str = "main"         # main | RH
    job_type: str = ""         # DS LD GI GO RH
    parent: str = ""           # RH 이면 딸린 본 작업
    home: str = ""             # 원래 담당 크레인
    assisted: bool = False     # 반대편 크레인이 대신 한 rehandle
    rh_time: float = 0.0       # 비교군: 본 작업 처리시간 안에 포함된 rehandle 시간
    ready: float = 0.0         # 트럭 도착 시각 a (모든 본 작업)
    truck_out: float = 0.0     # 트럭이 풀려나는 시각 (DS·GI: 시작+pick, LD·GO: 완료)
    seq: int = 0
    duration: float = 0.0

    def __post_init__(self):
        if not self.duration:
            self.duration = round(self.end - self.start, 6)


@dataclass
class MethodResult:
    method: str
    label: str
    status: str = "UNKNOWN"
    feasible: bool = False
    makespan: float | None = None
    objective: float | None = None       # 솔버 목적함수 값 (makespan 또는 GO 대기)
    objective_name: str = ""             # makespan | go_wait
    best_bound: float | None = None      # objective 의 하한
    mip_gap: float | None = None
    solve_time: float | None = None
    wall_time: float = 0.0
    num_vars: int | None = None
    num_constraints: int | None = None
    schedule: list[ScheduleEntry] = field(default_factory=list)
    crane_sequences: dict[str, list[str]] = field(default_factory=dict)
    metrics: dict[str, Any] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)
    error: str | None = None
    log: str = ""
    figures: list[str] = field(default_factory=list)

    def to_dict(self, include_log: bool = True, include_figures: bool = True) -> dict[str, Any]:
        data = asdict(self)
        if not include_log:
            data.pop("log", None)
        if not include_figures:
            data["num_figures"] = len(data.get("figures") or [])
            data.pop("figures", None)
        return data

    def finalize(self, t_hat: float | None = None) -> "MethodResult":
        """스케줄에서 연구 지표(GO 대기, 지원 rehandle, 유휴시간)를 계산한다.

        t_hat 을 주면 '원래 크레인이 지원을 기다린 시간' 의 빈 이동 시간을 뺀다."""
        if not self.schedule:
            return self
        self.schedule.sort(key=lambda e: (e.start, e.crane))
        by: dict[str, list[ScheduleEntry]] = {}
        for e in self.schedule:
            by.setdefault(e.crane, []).append(e)
        self.crane_sequences = {}
        for c, es in by.items():
            es.sort(key=lambda e: e.start)
            for i, e in enumerate(es, start=1):
                e.seq = i
            self.crane_sequences[c] = [e.task for e in es]

        end = max(e.end for e in self.schedule)
        if self.makespan is None:
            self.makespan = end
        span = self.makespan or end

        mains = [e for e in self.schedule if e.kind == "main"]
        tw = {e.task: e.truck_out - e.ready for e in mains}
        by_type = {}
        for e in mains:
            by_type.setdefault(e.job_type, []).append(tw[e.task])
        go = [e for e in mains if e.job_type == "GO"]
        waits = [tw[e.task] for e in go]
        internal = by_type.get("DS", []) + by_type.get("LD", [])
        external = by_type.get("GI", []) + by_type.get("GO", [])
        rh = [e for e in self.schedule if e.kind == "RH"]
        n_rh = self.metrics.get("n_rehandles", len(rh))
        n_assist = sum(1 for e in rh if e.assisted)
        busy = {c: round(sum(e.duration for e in es), 3) for c, es in by.items()}
        self.metrics.update({
            "n_jobs": sum(1 for e in self.schedule if e.kind == "main"),
            "n_rehandles": n_rh,
            "n_assisted": n_assist,
            "assist_rate": (round(n_assist / n_rh, 4) if n_rh else None),
            "assisted_by": {c: sum(1 for e in rh if e.assisted and e.crane == c) for c in by},
            "truck_wait_sum": round(sum(tw.values()), 4) if tw else None,
            "truck_wait_avg": round(sum(tw.values()) / len(tw), 4) if tw else None,
            "truck_wait_max": round(max(tw.values()), 4) if tw else None,
            "truck_wait_internal": round(sum(internal), 4) if internal else None,
            "truck_wait_external": round(sum(external), 4) if external else None,
            "truck_wait_by_type": {t: {"n": len(v), "sum": round(sum(v), 4),
                                       "avg": round(sum(v) / len(v), 4)} for t, v in by_type.items()},
            "go_count": len(go),
            "go_wait_sum": round(sum(waits), 4) if go else None,
            "go_wait_avg": round(sum(waits) / len(waits), 4) if go else None,
            "go_wait_max": round(max(waits), 4) if go else None,
            "busy_time_per_crane": busy,
            "idle_time_per_crane": {c: round(span - b, 3) for c, b in busy.items()},
            "finish_per_crane": {c: round(max(e.end for e in es), 3) for c, es in by.items()},
            "schedule_span": round(end, 3),
        })
        self.metrics.update(assist_effect(self.schedule, by, t_hat))
        return self


def assist_effect(schedule: list[ScheduleEntry], by: dict[str, list[ScheduleEntry]],
                  t_hat: float | None) -> dict[str, Any]:
    """지원이 의도대로 '겹쳐서' 일어났는지 보는 지표.

    assist_overlap   : 지원 rehandle 시간 중 원래 크레인이 자기 다른 작업을 하던 시간 (의도한 효과)
    assist_home_wait : 지원받은 본 작업 앞에서 원래 크레인이 rehandle 이 끝나기를 기다린 시간 (추정)
    """
    rh_all = [e for e in schedule if e.kind == "RH"]
    helped = [e for e in rh_all if e.assisted]
    if not rh_all:
        return {}
    overlap = 0.0
    for r in helped:
        for e in by.get(r.home, []):
            overlap += max(0.0, min(r.end, e.end) - max(r.start, e.start))
    assist_time = sum(e.end - e.start for e in helped)
    th = float(t_hat or 0)
    wait, jobs = 0.0, 0
    parents = {e.parent for e in helped}
    for j in schedule:
        if j.kind != "main" or j.task not in parents:
            continue
        jobs += 1
        home_seq = by.get(j.crane, [])
        prev = [e for e in home_seq if e.kind == "main" and e.end <= j.start + 1e-6 and e is not j]
        q_end, pos = (prev[-1].end, prev[-1].d) if prev else (0.0, j.o)
        mine = sorted((e for e in rh_all if e.parent == j.task), key=lambda e: e.start)
        ready = min([e.ready for e in mine] + [j.ready]) if mine else j.ready
        own, travel = 0.0, 0.0
        for e in mine:
            if e.crane == j.crane:
                own += e.end - e.start
                travel += abs(pos - e.o) * th
                pos = e.d
        travel += abs(pos - j.o) * th
        wait += max(0.0, j.start - max(q_end, ready) - own - travel)
    return {
        "assist_time": round(assist_time, 3),
        "assist_overlap": round(overlap, 3),
        "assist_overlap_rate": round(overlap / assist_time, 4) if assist_time else None,
        "assist_home_wait": round(wait, 3),
        "assist_jobs": jobs,
    }


class Method:
    key: str = "base"
    label: str = "Base"
    short: str = ""
    paper: str = ""
    solver: str = ""
    description: str = ""
    role: str = ""              # baseline | proposed
    options: dict[str, dict[str, Any]] = {}

    def available(self) -> tuple[bool, str]:
        return True, ""

    def check_compatibility(self, inst: Instance) -> list[str]:
        return []

    def solve(self, inst: Instance, options: dict[str, Any],
              context: dict[str, Any] | None = None) -> MethodResult:
        raise NotImplementedError

    def info(self) -> dict[str, Any]:
        ok, reason = self.available()
        return {
            "key": self.key, "label": self.label, "short": self.short or self.label,
            "paper": self.paper, "solver": self.solver, "description": self.description,
            "role": self.role, "options": self.options,
            "available": ok, "unavailable_reason": reason,
        }

    def _new_result(self) -> MethodResult:
        return MethodResult(method=self.key, label=self.label)
