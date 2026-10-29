"""
롤링 호라이즌 실험 (2026-10-28)
==============================

정보가 시간에 따라 들어오는 상황에서, 들어올 때마다 "그때까지 아는 작업만으로" 다시 계획한다.

정보 공개
  - 외부트럭(ET, 반입 GI · 반출 GO): 트럭이 도착하는 순간 알게 된다 → 반출 rehandle 은 도착 뒤에만
  - AGV(양하 DS · 적하 LD): 도착 H분(기본 60분) 전에 알게 된다 → 적하 rehandle 은 그때부터 미리 가능
본 작업 순서는 고정(predetermined). 크레인별로 "앞에서부터 공개된 작업까지"(접두부)만 계획에 넣는다
(순서가 고정이라, 아직 모르는 작업 뒤의 작업은 어차피 먼저 할 수 없다).

재계획 (작업 투입)
  - 시점: "새 정보마다" = ET 도착 · AGV 정보 공개 때마다,
          "일정 간격" = 첫 작업 시각부터 Δ분(기본 30)마다 — 그 사이 정보가 들어온 작업은 다음 시점에 한꺼번에 투입
  - 확정: 다음 재계획 전에 시작하는 작업만 계획대로 실행한 것으로 확정한다. 나머지는 다음 시점에 다시 계획
  - 크레인 상태: 마지막으로 확정한 작업이 끝나는 시각(ready)과 그 위치에서 출발
  - 이미 확정된 rehandle 이 아직 진행 중이면, 그 본 작업은 rehandle 이 끝난 뒤에만 시작 (release 로 반영)

한 시점의 계획은 안쪽 방법(창 규칙 v2 · 협업 MIP · 지원 없음)으로 푼다. 계획에는 상대 크레인의 진행 중 작업이
크레인 위치 · 비는 시각으로만 들어간다(근사). 대신 확정된 작업은 '실행' 단계에서 계획 순서대로 수행하며
상대 크레인과의 간섭(ETD)이 있으면 그만큼 늦춘다 → 최종 스케줄은 항상 노트북 제약을 지킨다(끝에서 다시 검사).
"""

from __future__ import annotations

import contextlib
import copy
import io
import time

from ..schema import Instance, Job, Block, validate
from .. import nbrunner
from .base import Method, ScheduleEntry
from .rules import EPS, tasks_of, verify, objective, init_pos, first_start, conflict, etd

AGV_TYPES = ("DS", "LD")


class _Cancel(Exception):
    pass


def _reveal(inst: Instance, job: Job, agv_ahead: float, et_ahead: float = 0.0) -> float:
    a = inst.ready(job)
    return max(0.0, a - (agv_ahead if job.type in AGV_TYPES else et_ahead))


class RollingMethod(Method):
    role = "heuristic"
    solver = "Python (롤링 호라이즌)"
    draw_with_nbchart = True

    def __init__(self, key: str, label: str, short: str, inner: Method, inner_fixed: dict | None = None,
                 description: str = ""):
        self.key, self.label, self.short = key, label, short
        self.inner = inner
        self.inner_fixed = dict(inner_fixed or {})
        self.description = description
        self.paper = "롤링 호라이즌 · 외부트럭은 도착 시, AGV 는 H분 전 정보 공개 · 새 정보마다 또는 일정 간격(예: 30분)으로 작업 투입·재계획"
        hidden = {"ld_known", "ld_lookahead_min", "rh_after_arrival", "draw_notebook_figure"} | set(self.inner_fixed)
        inner_opts = {k: v for k, v in (inner.options or {}).items() if k not in hidden}
        if "time_limit" in inner_opts:
            inner_opts["time_limit"] = dict(inner_opts["time_limit"], label="재계획 1회 제한 (s)",
                                            default=min(30, inner_opts["time_limit"].get("default", 30)))
        self.options = {
            "agv_info_min": {"type": "number", "default": 60, "label": "AGV 정보를 미리 아는 시간 H (분)"},
            "et_info_min": {"type": "number", "default": 0,
                            "label": "외부트럭 정보를 미리 아는 시간 (분, 0 = 도착해야 앎 · rehandle 은 어쨌든 도착 뒤)"},
            "replan_mode": {"type": "select", "choices": ["새 정보마다", "일정 간격"], "default": "새 정보마다",
                            "label": "작업 투입(재계획) 방식"},
            "replan_interval_min": {"type": "number", "default": 30,
                                    "label": "일정 간격일 때 투입 간격 (분, 첫 작업 시각부터)"},
            **inner_opts,
            "total_time_limit": {"type": "number", "default": 900,
                                 "label": "롤링 전체 제한 (s, 0 = 없음) — 넘으면 남은 재계획은 빠른 규칙으로 마무리"},
            "plan_jobs_max": {"type": "number", "default": 12,
                              "label": "재계획마다 크레인별로 앞에서 몇 개까지 계획 (0 = 아는 것 전부)"},
            "draw_notebook_figure": {"type": "checkbox", "default": True, "label": "노트북 원본 차트 그리기 (최종 스케줄)"},
        }

    def available(self):
        return self.inner.available()

    # ------------------------------------------------------------------
    def solve(self, inst, options, context=None):
        t0 = time.perf_counter()
        res = self._new_result()
        invalid = validate(inst)["errors"]
        if invalid:
            res.status, res.error = "ERROR", "; ".join(invalid)
            return res
        name = options.get("objective", "truck_wait")
        H = max(0.0, float(options.get("agv_info_min", 60) or 0)) * 60.0
        gap = max(0.0, float(options.get("replan_gap", 0) or 0))      # (예전 옵션) 새 정보마다 방식의 최소 간격 (초)
        mode = str(options.get("replan_mode") or "새 정보마다")
        periodic = mode in ("일정 간격", "periodic")
        step = max(1.0, float(options.get("replan_interval_min", 30) or 30)) * 60.0
        total = max(0.0, float(options.get("total_time_limit", 900) or 0))
        plan_max = max(0, int(float(options.get("plan_jobs_max", 12) or 0)))
        E = max(0.0, float(options.get("et_info_min", 0) or 0)) * 60.0
        p = inst.params
        inner_opt = {k: v for k, v in options.items()
                     if k not in ("agv_info_min", "et_info_min", "replan_gap", "replan_mode", "replan_interval_min",
                                  "total_time_limit", "plan_jobs_max", "draw_notebook_figure")}
        inner_opt.update(self.inner_fixed)
        inner_opt.update(rh_after_arrival=True, ld_known=True, ld_lookahead_min=H / 60.0,
                         draw_notebook_figure=False, objective=name)

        jobs = {j.id: j for j in inst.seq_sc + inst.seq_lc}
        home = {j.id: ("SC" if j in inst.seq_sc else "LC") for j in jobs.values()}
        seqs = {"SC": [j.id for j in inst.seq_sc], "LC": [j.id for j in inst.seq_lc]}
        reveal = {jid: _reveal(inst, j, H, E) for jid, j in jobs.items()}
        # 재계획(작업 투입) 시점
        #  · 새 정보마다: 정보가 들어오는 시각마다 (replan_gap > 0 이면 그만큼 띄움)
        #  · 일정 간격: 첫 정보 시각 t0 부터 t0, t0+Δ, t0+2Δ, … — 그 사이 들어온 정보는 다음 시점에 한꺼번에 투입
        times = sorted(set(reveal.values()))
        epochs = []
        if periodic:
            k = 0
            while True:
                epochs.append(times[0] + k * step)
                if epochs[-1] >= times[-1] - EPS:
                    break
                k += 1
        else:
            for t in times:
                if not epochs:
                    epochs.append(t)
                elif t > epochs[-1] + EPS:
                    nxt = max(t, epochs[-1] + gap)
                    if nxt > epochs[-1] + EPS:
                        epochs.append(nxt)
            epochs = sorted(set(epochs))
        # 시점마다 새로 투입되는 작업 수 (그 시점까지 정보가 들어온 작업 중 앞 시점에 없던 것)
        added, prev = [], -1e18
        for t in epochs:
            added.append(sum(1 for r in reveal.values() if prev + EPS < r <= t + EPS))
            prev = t

        # 전체 작업 (원래 시각) — 실행 시뮬레이션 · 최종 검사에 쓴다
        tasks, _, prec = tasks_of(inst, True, True, H)
        preds = {k: set() for k in tasks}
        for x, y in prec:
            preds[y].add(x)
        executed: dict[str, float] = {}          # 실제 시작 시각 (확정 + 간섭 반영)
        crane_of: dict[str, str] = {}
        order: list[str] = []
        rh_done = {jid: 0 for jid in jobs}       # 확정된 rehandle 개수 (위에서부터)
        n_solves, inner_time, n_push, n_delay, n_fallback = 0, 0.0, 0, 0, 0
        status_inner = []

        def execute(plan: list[tuple[float, str, str]], t_dec: float):
            """확정된 작업을 계획한 시작 순서대로 실제로 수행한다.
            각 작업은 (계획 시각, 트럭/AGV 도착·선행 rehandle, 같은 크레인 이전 작업, 상대 크레인과의 ETD) 중 가장 늦은 때 시작.
            그래서 계획이 상대 크레인의 진행 중 작업을 몰라도 실제 스케줄은 노트북 제약을 지킨다."""
            nonlocal n_delay
            for planned, tid, c in sorted(plan):
                q = tasks[tid]
                st = max(planned, q.ready, t_dec, first_start(inst, c, q.o))   # 계획 시각보다 앞당기지는 않는다
                rivals = []
                for prev in order:
                    pq, pc = tasks[prev], crane_of[prev]
                    if pc == c:
                        st = max(st, executed[prev] + pq.duration + abs(pq.d - q.o) * p.t_hat)
                    elif conflict(pq, q, pc, p):
                        rivals.append(prev)
                for prev in preds[tid]:
                    st = max(st, executed[prev] + tasks[prev].duration)
                # 상대 크레인과 겹치는 작업: 노트북 ETD 는 '둘 중 한 방향' 만 지키면 된다
                # (q 가 prev 뒤 : st ≥ prev 끝 + ETD(prev→q),  q 가 prev 앞 : prev 시작 ≥ q 끝 + ETD(q→prev)).
                # 계획대로면 이미 한쪽이 성립하고, 늦춰져서 둘 다 깨지면 q 를 prev 뒤로 미룬다.
                changed = True
                while changed:
                    changed = False
                    for prev in rivals:
                        pq, pc = tasks[prev], crane_of[prev]
                        after = executed[prev] + pq.duration + etd(pq, q, pc, p)
                        if st + 1e-9 < after and executed[prev] + 1e-9 < st + q.duration + etd(q, pq, c, p):
                            st, changed = after, True
                if st > planned + 1e-6:
                    n_delay += 1
                executed[tid], crane_of[tid] = st, c
                order.append(tid)

        def crane_state(c, t):
            mine = [k for k in order if crane_of[k] == c]
            if not mine:
                return t, init_pos(inst, c)
            last = max(mine, key=lambda k: executed[k] + tasks[k].duration)
            return max(t, executed[last] + tasks[last].duration), tasks[last].d

        # 시간 관리: 전체 제한(total) 안에서 재계획 1회 제한을 남은 시간에 맞춰 줄이고,
        # 다 쓰면 남은 재계획은 빠른 방법(창 규칙 v2 1초, 안 되면 지원 없는 디코더)으로 마무리한다
        tl_user = float(options.get("time_limit", inner_opt.get("time_limit", 30)) or 30)
        fast = None
        if self.inner.key != "window_rule_v2":
            from .window_rule import WindowRuleV2
            fast = WindowRuleV2()
        n_fast, n_trunc, log_every = 0, 0, max(1, len(epochs) // 40)

        def plan_once(ei, t, t_next):
            """시점 t 에서 한 번 계획하고 확정. 반환: (확정한 작업 수, 잘림 여부) — 실패 시 예외"""
            nonlocal n_solves, inner_time, n_push, n_fallback, n_fast, n_trunc
            sub_seq, truncated = {}, False
            for c in ("SC", "LC"):
                lst = []
                for jid in seqs[c]:
                    if reveal[jid] > t + EPS:
                        break
                    if jid not in executed:
                        lst.append(jid)
                if plan_max and len(lst) > plan_max:
                    lst, truncated = lst[:plan_max], True
                sub_seq[c] = lst
            if not sub_seq["SC"] and not sub_seq["LC"]:
                return 0, False
            n_trunc += truncated
            # 부분 인스턴스 (시각은 t 를 0 으로 옮김)
            sub = copy.deepcopy(inst)
            sub.id, sub.name = inst.id + f"@{t:.0f}", inst.name + f" @t={t:.0f}"
            (sc_r, sc_pos), (lc_r, lc_pos) = crane_state("SC", t), crane_state("LC", t)
            # 두 크레인의 '마지막 위치' 는 서로 다른 시각의 위치라 안전거리보다 가까울 수 있다.
            # 먼저 빈 크레인은 그동안 비켜 있었던 것이므로 안전거리만큼 밀어 둔다 (끝 TP 를 넘으면 반대 크레인을)
            dlt = p.safety_margin + 1
            n_tp = inst.block.n_bay + 1
            if lc_pos - sc_pos < dlt:
                if (lc_r <= sc_r and sc_pos + dlt <= n_tp) or sc_pos - dlt < 0:
                    lc_pos = min(n_tp, sc_pos + dlt)
                    sc_pos = min(sc_pos, lc_pos - dlt)
                else:
                    sc_pos = max(0, lc_pos - dlt)
                    lc_pos = max(lc_pos, sc_pos + dlt)
                n_push += 1
            sub.block = Block(n_bay=inst.block.n_bay, sc_init=sc_pos, lc_init=lc_pos,
                              sc_ready=sc_r - t, lc_ready=lc_r - t)
            offset = {}
            for c in ("SC", "LC"):
                out = []
                for jid in sub_seq[c]:
                    j = jobs[jid]
                    k0 = rh_done[jid]
                    dests = (j.rh_d or [j.bay] * j.r_c)[k0:]
                    rel = inst.ready(j)
                    if k0:                               # 확정된 rehandle 이 끝나야 본 작업 시작
                        last = f"{jid}_r{k0}"
                        rel = max(rel, executed[last] + tasks[last].duration)
                    out.append(Job(jid, j.type, j.bay, r_c=len(dests),
                                   rh_d=(dests if any(d != j.bay for d in dests) else None),
                                   a=round(max(0.0, rel - t), 6)))
                    offset[jid] = k0
                if c == "SC":
                    sub.seq_sc = out
                else:
                    sub.seq_lc = out
            # 이번 재계획에 줄 시간
            left = total - (time.perf_counter() - t0) if total else None
            method, opt = self.inner, dict(inner_opt)
            if left is not None:
                share = 2.0 * left / max(1, len(epochs) - ei)       # 남은 시간을 남은 투입 수로 (넉넉히 2배)
                opt["time_limit"] = max(1.0, min(tl_user, share))
                if left <= 0:
                    method = fast or self.inner
                    opt["time_limit"] = 1.0
                    n_fast += 1
            ti = time.perf_counter()
            with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                r = method.solve(sub, opt)
            dt = time.perf_counter() - ti
            inner_time += dt
            n_solves += 1
            status_inner.append(r.status)
            if nbrunner.cancelled():
                raise _Cancel(f"사용자가 취소함 (투입 {ei + 1}/{len(epochs)}번째 재계획 중, t={t:.0f})")
            if r.feasible and r.schedule:
                planned = [(e.start, e.task, e.crane, e.kind) for e in r.schedule]
            else:
                # 안쪽 방법이 못 푸는 부분 문제(아주 작거나 · 라이선스 크기 제한 등)는 지원 없이 같은 디코더로 시각만
                planned = _fallback_plan(sub, H, name)
                if planned is None:
                    raise RuntimeError(f"t={t:.0f} 재계획 실패 ({r.status}): {r.error or ''}"[:500])
                n_fallback += 1
            # 다음 정보 전에 시작하는 작업만 확정 → 실제 수행
            plan = []
            for e_start, e_task, e_crane, e_kind in planned:
                st = e_start + t
                if st >= t_next - EPS:
                    continue
                tid = e_task
                if e_kind == "RH":
                    jid, k = e_task.rsplit("_r", 1)
                    kk = int(k) + offset[jid]
                    tid = f"{jid}_r{kk}"
                    rh_done[jid] = max(rh_done[jid], kk)
                plan.append((st, tid, e_crane))
            execute(plan, t)
            if n_solves % log_every == 0 or ei == len(epochs) - 1:
                print(f"[롤링] 투입 {ei + 1}/{len(epochs)} t={t:.0f} · 계획 SC {len(sub_seq['SC'])} · LC {len(sub_seq['LC'])}"
                      f"{' (잘림)' if truncated else ''} · {r.status} {dt:.1f}s · 확정 {len(plan)} · 누적 {time.perf_counter() - t0:.0f}s"
                      + (f" / 전체 제한 {total:.0f}s" if total else ""), flush=True)
            return len(plan), truncated

        try:
            for ei, t in enumerate(epochs):
                if nbrunner.cancelled():
                    raise _Cancel(f"사용자가 취소함 (투입 {ei + 1}/{len(epochs)}번째, t={t:.0f} 전에 멈춤)")
                t_next = epochs[ei + 1] if ei + 1 < len(epochs) else float("inf")
                # 계획 범위를 잘랐으면, 확정이 계속 나오는 동안 같은 시점에서 다음 묶음을 이어서 계획
                while True:
                    n_commit, truncated = plan_once(ei, t, t_next)
                    if not truncated or n_commit == 0:
                        break
        except _Cancel as e:
            res.status, res.error = "CANCELLED", str(e)
            res.wall_time = time.perf_counter() - t0
            return res
        except RuntimeError as e:
            res.status, res.error = "ERROR", str(e)
            return res

        missing = [j for j in tasks if j not in executed]
        if missing:
            res.status, res.error = "ERROR", f"확정되지 않은 작업: {missing[:5]}"
            return res

        # 최종 스케줄 검사 (전체 노트북 제약) · 지표
        starts = executed
        rseqs = {c: [k for k in sorted(order, key=lambda k: executed[k]) if crane_of[k] == c] for c in ("SC", "LC")}
        errors = verify(inst, tasks, rseqs, prec, starts, True)
        res.wall_time = res.solve_time = time.perf_counter() - t0
        res.objective_name = name
        for k in order:
            q, c, st = tasks[k], crane_of[k], executed[k]
            res.schedule.append(ScheduleEntry(
                task=k, crane=c, o=q.o, d=q.d, start=st, end=st + q.duration,
                duration=q.duration, kind="RH" if q.type == "RH" else "main", job_type=q.type,
                parent=q.parent, home=q.home, assisted=c != q.home, ready=q.ready,
                truck_out=st + (p.pick if q.type in ("DS", "GI") else q.duration)))
        res.crane_sequences = rseqs
        res.feasible = not errors
        res.status = "ROLLING" if res.feasible else "ERROR"
        if errors:
            res.error = "전체 스케줄 검사 실패: " + "; ".join(errors[:10])
        res.objective = objective(inst, tasks, starts, name)
        res.metrics.update(
            n_rehandles=inst.num_rehandles(), rolling=True, agv_info_min=H / 60.0, et_info_min=E / 60.0, replans=n_solves,
            inner_method=self.inner.key, inner_time=round(inner_time, 3), position_pushes=n_push,
            replan_mode="일정 간격" if periodic else "새 정보마다",
            replan_interval_min=(step / 60.0) if periodic else None,
            replan_times=[round(t, 3) for t in epochs], replan_added=added,
            delayed_in_execution=n_delay, total_time_limit=total or None, plan_jobs_max=plan_max or None,
            fast_replans=n_fast, truncated_replans=n_trunc, fallback_plans=n_fallback, inner_status={s: status_inner.count(s) for s in set(status_inner)},
            rh_after_arrival=True, ld_lookahead=H)
        res.notes.append((f"작업 투입: 첫 작업 시각부터 {step / 60:g}분마다 {len(epochs)}번 (그 사이 정보는 다음 투입 때 한꺼번에)"
                          if periodic else f"작업 투입: 새 정보가 들어올 때마다 {len(epochs)}번")
                         + f" · 투입당 작업 평균 {sum(added) / max(1, len(added)):.1f}개.")
        if n_fast:
            res.notes.append(f"⚠ 전체 제한 {total:.0f}s 를 다 써서 마지막 재계획 {n_fast}번은 빠른 규칙"
                             f"({'창 규칙 v2 1초' if fast else '1초 제한'})으로 마무리했습니다.")
        if plan_max:
            res.notes.append(f"계획 범위: 재계획마다 크레인별 앞 {plan_max}개까지 (잘린 재계획 {n_trunc}번 — 확정이 나오는 동안 같은 시점에서 이어서 계획).")
        res.notes.append(f"롤링 호라이즌: 재계획 {n_solves}회 (안쪽 방법 {self.inner.label}), AGV 정보 {H / 60:.0f}분 전 공개, "
                         + (f"외부트럭 {E / 60:.0f}분 전 공개 (rehandle 은 도착 뒤). " if E else "외부트럭은 도착 시 공개. ") + f"안쪽 계산 {inner_time:.1f}s.")
        res.notes.append(f"실행: 확정된 작업은 계획한 순서대로 수행하고, 상대 크레인의 진행 중 작업과 간섭하면 그만큼 늦춘다 "
                         f"(늦춰진 작업 {n_delay}개). 최종 스케줄은 노트북 제약(ETD 포함) 검사 "
                         + ("통과." if not errors else f"실패: {'; '.join(errors[:3])}"))
        res.notes.append("비교 상한: 같은 조건(반출 RH 는 도착 뒤, 적하 RH 는 AGV 도착 H분 전부터)에서 모든 정보를 처음부터 아는 "
                         "협업 MIP = 협업 모델에 'AGV 도착 H분 전부터' 옵션을 켠 결과.")
        return res.finalize(t_hat=float(p.t_hat))


def _fallback_plan(sub: Instance, H: float, name: str):
    """지원 없이(rehandle 은 원래 크레인) 노트북 제약대로 시각만 계산. [(start, task, crane, kind)]"""
    from .window_rule import decode as wdecode
    tasks, seqs, prec = tasks_of(sub, True, True, H)
    if not tasks:
        return []
    starts = wdecode(sub, tasks, seqs, prec, name, True, None)
    if starts is None:
        return None
    return [(starts[k], k, c, "RH" if tasks[k].type == "RH" else "main") for c, seq in seqs.items() for k in seq]


def rolling_methods(methods: list[Method]) -> list[Method]:
    """레지스트리용: 롤링 · 창 규칙 v2 / 롤링 · 협업 MIP / 롤링 · 지원 없음."""
    by = {m.key: m for m in methods}
    out = []
    if "window_rule_v2" in by:
        out.append(RollingMethod(
            "rolling_window_v2", "롤링 · Window–Slack ver2", "롤링 v2", by["window_rule_v2"],
            description="정보가 들어올 때마다 Window–Slack ver2 로 다시 계획 (AGV H분 전 · 외부트럭 도착 시 공개)"))
        out.append(RollingMethod(
            "rolling_nohelp", "롤링 · 지원 없음", "롤링 지원X", by["window_rule_v2"],
            inner_fixed={"help_go": False, "help_ld": False, "gap_search": False, "recheck": False},
            description="같은 롤링 설정에서 반대편 크레인 지원을 끈 비교군 (rehandle 은 원래 크레인이)"))
    if "coop" in by:
        out.append(RollingMethod(
            "rolling_coop", "롤링 · 협업 MIP", "롤링 MIP", by["coop"],
            description="정보가 들어올 때마다 협업 MIP 를 짧은 시간 제한으로 다시 풂 (재계획 1회 제한 옵션)"))
    return out
