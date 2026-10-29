"""
Window–Slack 지원 규칙 (규칙 0 · 1 · 2 · 3 · 4)
==============================================

analysis/assist_cases/assist_rules.html 에 정리한 규칙을 그대로 옮긴 solver-free 휴리스틱.
본 작업의 할당·순서는 고정(predetermined), rehandle 만 반대편 크레인에게 넘길지 정한다.

  규칙 0  rehandle 은 그 본 작업의 트럭이 온 뒤에만 시작 (옵션 rh_after_arrival, 기본 켬)
  규칙 1  원래 크레인(H)이 아직 바쁠 때만 돕는다: 트럭 도착 a_j < A_H (H 가 bay b 에 올 수 있는 시각)
  규칙 2  돕는 시간 창은 ETD 로 정해진다 → 아래 decode() 가 노트북과 같은 ETD 식으로 실제 시작 시각을 계산
  규칙 3  지원 크레인(G)의 다음 자기 트럭이 이미 와 있으면 돕지 않는다
  규칙 4  위 rehandle(r1)부터 하나씩 넘긴다. 개수 상한 없음. 다음 중 하나면 거기서 멈춘다
          (가) G 의 다음 자기 트럭이 rehandle 시작 전에 도착 (자기 일로 돌아감)
          (나) 넘겨도 본 작업 j 가 빨라지지 않음 (H 가 하는 편이 빠름)
  (선택) 목적값 보호: 넘겼더니 목적값이 나빠지면 멈춘다 (규칙 밖, 기본 끔)

규칙 5(내려놓는 bay 를 b∓δ 로 바꾸기)는 인스턴스의 rehandle 목적 bay 를 바꾸므로
협업 MIP 와 같은 문제가 아니게 되어 넣지 않았다.
"""

from __future__ import annotations

import time
from collections import Counter

from ..schema import validate
from .. import nbrunner
from .base import Method, ScheduleEntry
from .rules import EPS, tasks_of, verify, objective, init_pos, etd, conflict, first_start


# ----------------------------------------------------------------------
# 디코더: 고정된 크레인별 순서(seqs)에서 시작 시각을 계산한다.
# rules.decode 와 같은 직렬 생성(가장 빨리 시작할 수 있는 작업부터, 노트북 ETD 식)인데,
# 두 크레인의 머리 작업이 서로 간섭할 때 "누가 먼저 지나갈지"를 탐욕적으로 정하지 않고
# 두 경우를 끝까지 이어서 계산해 본 뒤(rollout) 목적값이 작은 쪽을 고른다.
# 탐욕 디코더만 쓰면 무지원 스케줄부터 MIP 보다 크게 나빠져서(실험 2026-10-13) 넣었다.
# ----------------------------------------------------------------------
def _setup(tasks, seqs, prec):
    assigned = {t: c for c, seq in seqs.items() for t in seq}
    preds = {t: set() for t in tasks}
    for seq in seqs.values():
        for a, b in zip(seq, seq[1:]):
            preds[b].add(a)
    for a, b in prec:
        preds[b].add(a)
    return assigned, preds


class _Ctx:
    """디코더가 반복해서 쓰는 값. 2026-10-28 속도 개선 (결과는 예전과 똑같음, 테스트로 확인):
    - 시작 가능 작업 = 각 크레인 순서의 맨 앞 작업뿐 (순서가 선행관계에 들어 있으므로)
    - 같은 크레인 제약은 바로 앞 작업만 보면 된다 (이동시간이 삼각부등식을 만족)
    - 상대 크레인 작업은 최근 것부터 보다가, 끝 + ETD 최댓값이 지금 하한보다 작아지면 멈춘다
      (한 크레인의 작업은 놓인 순서대로 끝나므로 그 앞 작업들은 하한을 올릴 수 없음)"""

    def __init__(self, inst, tasks, seqs, prec):
        self.inst, self.tasks, self.p = inst, tasks, inst.params
        self.assigned, self.preds = _setup(tasks, seqs, prec)
        self.seqs = {c: list(s) for c, s in seqs.items()}
        self.other = {"SC": "LC", "LC": "SC"}
        p = inst.params
        self.emax = (inst.block.n_bay + 1 + p.safety_margin + 1) * p.t_hat + 1e-9
        self.lb = {t: max(q.ready, first_start(inst, self.assigned[t], q.o)) for t, q in tasks.items()}

    def ready(self, starts, placed):
        out = []
        for c, seq in self.seqs.items():
            k = len(placed.get(c, ()))
            if k < len(seq):
                t = seq[k]
                if self.preds[t] <= starts.keys():
                    out.append(t)
        return out

    def earliest(self, starts, placed, tid):
        tasks, p = self.tasks, self.p
        t, c = tasks[tid], self.assigned[tid]
        st = self.lb[tid]
        mine = placed.get(c)
        if mine:
            q = tasks[mine[-1]]
            st = max(st, starts[mine[-1]] + q.duration + abs(q.d - t.o) * p.t_hat)
        for prev in self.preds[tid]:
            st = max(st, starts[prev] + tasks[prev].duration)
        oc = self.other.get(c)
        for prev in reversed(placed.get(oc, ())):
            q = tasks[prev]
            end = starts[prev] + q.duration
            if end + self.emax <= st:
                break
            if conflict(q, t, oc, p):
                st = max(st, end + etd(q, t, oc, p))
        return st

    def complete(self, starts, placed, n_done):
        """남은 작업을 가장 빨리 시작할 수 있는 순서로 채운다 (탐욕)."""
        starts = dict(starts)
        placed = {c: list(v) for c, v in placed.items()}
        n = len(self.tasks)
        while n_done < n:
            ready = self.ready(starts, placed)
            if not ready:
                return None
            e = {t: self.earliest(starts, placed, t) for t in ready}
            ch = min(ready, key=lambda t: (e[t], self.assigned[t] != "SC", t))
            starts[ch] = e[ch]
            placed.setdefault(self.assigned[ch], []).append(ch)
            n_done += 1
        return starts


def decode(inst, tasks, seqs, prec, name="truck_wait", rollout=True, deadline=None):
    cx = _Ctx(inst, tasks, seqs, prec)
    if not rollout:
        return cx.complete({}, {}, 0)
    p = inst.params
    starts, placed, done = {}, {}, 0
    while done < len(tasks):
        ready = cx.ready(starts, placed)
        if not ready:
            return None
        e = {t: cx.earliest(starts, placed, t) for t in ready}
        ch = min(ready, key=lambda t: (e[t], cx.assigned[t] != "SC", t))
        rivals = [t for t in ready if cx.assigned[t] != cx.assigned[ch]
                  and (conflict(tasks[ch], tasks[t], cx.assigned[ch], p) or conflict(tasks[t], tasks[ch], cx.assigned[t], p))]
        if rivals and (deadline is None or time.perf_counter() < deadline):
            best = None
            for c in [ch] + rivals:
                s2 = dict(starts); s2[c] = e[c]
                pl2 = {k: list(v) for k, v in placed.items()}
                pl2.setdefault(cx.assigned[c], []).append(c)
                full = cx.complete(s2, pl2, done + 1)
                if full is None:
                    continue
                v = objective(inst, tasks, full, name)
                if best is None or v < best[0] - EPS:
                    best = (v, c)
            if best:
                ch = best[1]
        starts[ch] = e[ch]
        placed.setdefault(cx.assigned[ch], []).append(ch)
        done += 1
    return starts

STOP_LABEL = {
    "rule1": "규칙 1 · 원래 크레인이 이미 비어 있음",
    "rule3": "규칙 3·4(가) · 지원 크레인 자기 트럭 도착",
    "rule4b": "규칙 4(나) · 원래 크레인이 하는 편이 빠름",
    "guard": "목적값 보호 · 목적값이 나빠짐",
    "infeasible": "ETD·선행 제약을 만족하는 삽입 위치 없음",
    "all": "모두 지원",
    "time": "시간 제한",
    "nogap": "ver2 빈틈 탐색 · 규칙 3·4(나)·재검사를 지키는 틈 없음",
    "recheck": "ver2 사후 재검사 · 넣으면 앞서 한 지원이 트럭 도착 뒤로 밀림",
}


class WindowRule(Method):
    key = "window_rule"
    label = "Window–Slack 지원 규칙"
    short = "창 규칙"
    role = "heuristic"
    solver = "Python (solver-free)"
    paper = "assist_rules.html 규칙 0~4 (상황별 지원 분석에서 도출)"
    description = ("무지원 스케줄에서 시작해, 원래 크레인이 바쁠 때(규칙 1) 반대편 크레인이 "
                   "위 rehandle 부터 하나씩 넘겨받는다(규칙 4). 지원 크레인 자기 트럭이 오면 멈춘다(규칙 3). "
                   "시작 시각은 노트북과 같은 ETD 로 계산(규칙 2).")
    options = {
        "time_limit": {"type": "number", "default": 30, "label": "휴리스틱 제한 (s)"},
        "rh_after_arrival": {"type": "checkbox", "default": True,
                             "label": "규칙 0 · rehandle 은 트럭 도착 뒤에만 시작 (RH_AFTER_ARRIVAL)"},
        "help_go": {"type": "checkbox", "default": True, "label": "반출(GO) rehandle 지원 (SC 가 LC 를 도움)"},
        "help_ld": {"type": "checkbox", "default": True, "label": "적하(LD) rehandle 지원 (LC 가 SC 를 도움)"},
        "rule1": {"type": "checkbox", "default": True,
                  "label": "규칙 1 · 원래 크레인이 바쁠 때만 지원 (끄면 언제든 시도)"},
        "objective_guard": {"type": "checkbox", "default": False,
                            "label": "목적값 보호 · 목적값이 나빠지는 지원은 하지 않음 (규칙 밖)"},
        "draw_notebook_figure": {"type": "checkbox", "default": True, "label": "노트북 원본 차트 그리기"},
        "ld_known": {"type": "checkbox", "default": False,
                     "label": "적하(LD) rehandle 은 AGV 도착 H분 전부터 미리 가능 (LD_LOOKAHEAD)"},
        "ld_lookahead_min": {"type": "number", "default": 30, "label": "H: AGV 도착을 미리 아는 시간 (분)"},
        "rollout": {"type": "checkbox", "default": True,
                    "label": "간섭 순서를 끝까지 계산해 보고 결정 (끄면 빠른 탐욕 디코더)"},
    }

    gap_search_default = False     # ver2: 첫 자리에서 막히면 지원 크레인의 다음 빈틈들을 차례로 시도
    recheck_default = False        # ver2: 넣을 때마다 앞서 넣은 지원이 규칙 3 을 어기게 되지 않는지 다시 확인
    block_default = False          # ver2 선택: 하나씩으로는 안 될 때 남은 rehandle 을 묶어서 한 틈에 넘겨 보기

    @staticmethod
    def _late_helps(tasks, seqs, starts):
        """지원 rehandle 중, 그 크레인의 다음 자기 본 작업 트럭이 이미 도착한 뒤에 시작한 것들."""
        out = set()
        for c, seq in seqs.items():
            for k, t in enumerate(seq):
                q = tasks[t]
                if q.type != "RH" or q.home == c:
                    continue
                nxt = next((x for x in seq[k + 1:] if tasks[x].type != "RH"), None)
                if nxt is not None and tasks[nxt].ready <= starts[t] + EPS:
                    out.add(t)
        return out

    # ------------------------------------------------------------------
    def solve(self, inst, options, context=None):
        t0 = time.perf_counter()
        res = self._new_result()
        invalid = validate(inst)["errors"]
        if invalid:
            res.status, res.error = "ERROR", "; ".join(invalid)
            return res
        name = options.get("objective", "truck_wait")
        if name not in ("makespan", "truck_wait", "go_wait"):
            raise ValueError("invalid objective")
        rh_arr = bool(options.get("rh_after_arrival", True))
        use_rule1 = bool(options.get("rule1", True))
        guard = bool(options.get("objective_guard", False))
        roll = bool(options.get("rollout", True))
        limit = max(.001, float(options.get("time_limit", 30)))
        deadline = t0 + limit
        allow = {"GO": bool(options.get("help_go", True)), "LD": bool(options.get("help_ld", True))}
        gap_search = bool(options.get("gap_search", self.gap_search_default))
        recheck = bool(options.get("recheck", self.recheck_default))
        max_gaps = max(1, int(options.get("max_gaps", 12) or 12))
        block = bool(options.get("block_move", self.block_default))
        p = inst.params

        from .coop import ld_lookahead_seconds
        ld_h = ld_lookahead_seconds(options)
        tasks, seqs, prec = tasks_of(inst, True, rh_arr, ld_h)
        starts = decode(inst, tasks, seqs, prec, name, roll, deadline)
        if starts is None or verify(inst, tasks, seqs, prec, starts, True):
            res.status, res.error = "ERROR", "무지원 초기 스케줄을 만들지 못했습니다."
            return res
        val0 = val = objective(inst, tasks, starts, name)

        # 처리 순서: 무지원 스케줄에서 본 작업이 시작하는 순서
        jobs = [j for jobs_ in (inst.seq_sc, inst.seq_lc) for j in jobs_
                if j.r_c and j.type in allow and allow[j.type]]
        jobs.sort(key=lambda j: starts[j.id])

        decisions, trials, stopped = [], 0, False
        for j in jobs:
            H = "SC" if j.type == "LD" else "LC"
            G = "LC" if H == "SC" else "SC"
            rids = [f"{j.id}_r{k}" for k in range(1, j.r_c + 1)]
            r1 = tasks[rids[0]]
            # A_H : 원래 크레인이 r1 직전 작업을 끝내고 bay b 에 올 수 있는 시각
            seqH = seqs[H]
            pos = seqH.index(rids[0])
            if pos:
                prev = tasks[seqH[pos - 1]]
                A_H = starts[prev.id] + prev.duration + abs(prev.d - r1.o) * p.t_hat
            else:
                A_H = first_start(inst, H, r1.o)
            rec = {"job": j.id, "type": j.type, "home": H, "helper": G, "k": j.r_c,
                   "a_j": inst.ready(j), "A_H": round(A_H, 3), "helped": 0,
                   "start_before": round(starts[j.id], 3)}
            if use_rule1 and r1.ready >= A_H - EPS:
                rec.update(stop="rule1", start_after=rec["start_before"])
                decisions.append(rec)
                continue
            last_slot = None
            reason = "all"
            ri = 0
            while ri < len(rids):
                rid = rids[ri]
                if nbrunner.cancelled() or time.perf_counter() - t0 >= limit:
                    stopped, reason = True, "time"
                    break
                r = tasks[rid]
                base = list(seqs[G])
                if last_slot is None:
                    # 첫 자리: 지원 크레인이 r 을 시작할 수 있게 되는 시점(r.ready) 이후 첫 작업 앞
                    first = next((k for k, t in enumerate(base) if starts[t] >= r.ready - EPS), len(base))
                else:
                    first = last_slot + 1          # 바로 앞에서 넘겨받은 rehandle 다음
                slots = [first]
                if gap_search:
                    # 빈틈: 지원 크레인 자기 본 작업 바로 뒤 자리들 (자기 작업과 자기 작업 사이)
                    for k in range(first + 1, len(base) + 1):
                        if len(slots) >= max_gaps:
                            break
                        prev = base[k - 1]
                        if tasks[prev].type == "RH" or tasks[prev].home != G:
                            continue
                        if starts[prev] >= starts[j.id] - EPS:   # j 가 시작한 뒤의 틈은 소용없음
                            break
                        slots.append(k)
                before_late = self._late_helps(tasks, seqs, starts) if recheck else set()
                first_reason, chosen = None, None
                sizes = [1] + (list(range(2, len(rids) - ri + 1)) if block else [])
                tries = [(gi, slot, n) for n in sizes for gi, slot in enumerate(slots)]
                for gi, slot, n in tries:
                    grp = rids[ri:ri + n]
                    cand = {c: [t for t in sq if t not in grp] for c, sq in seqs.items()}
                    for k2, g_ in enumerate(grp):
                        cand[G].insert(slot + k2, g_)
                    trial = decode(inst, tasks, cand, prec, name, roll, deadline)
                    trials += 1
                    why = None
                    if trial is None or verify(inst, tasks, cand, prec, trial, True):
                        why = "infeasible"
                    else:
                        # 규칙 3 · 4(가): 지원 크레인의 다음 자기 본 작업 트럭이 이미 와 있으면 그 자리는 안 됨
                        nxt = next((t for t in cand[G][slot + n:] if tasks[t].type != "RH"), None)
                        if nxt is not None and tasks[nxt].ready <= trial[grp[-1]] + EPS:
                            why = "rule3"
                        # 규칙 4(나): 넘겨도 j 가 빨라지지 않으면 안 됨
                        elif trial[j.id] >= starts[j.id] - EPS:
                            why = "rule4b"
                        # ver2 사후 재검사: 앞서 넣은 지원이 이번 삽입으로 트럭 도착 뒤로 밀리면 안 됨
                        elif recheck and (self._late_helps(tasks, cand, trial) - before_late - set(grp)):
                            why = "recheck"
                        else:
                            tv = objective(inst, tasks, trial, name)
                            if guard and tv > val + EPS:
                                why = "guard"
                    rec.setdefault("tries", []).append(
                        ["+".join(g_.rsplit("_", 1)[-1] for g_ in grp), (base[slot - 1] if slot else "-"),
                         round(trial[grp[0]], 1) if trial else None, why or "ok"])
                    if why is None:
                        chosen = (cand, trial, tv, slot, gi, n)
                        break
                    first_reason = first_reason or why
                if chosen is None:
                    reason = first_reason if len(slots) == 1 else "nogap"
                    break
                seqs, starts, val, last_slot, gi, n = chosen
                last_slot += n - 1
                rec["helped"] += n
                ri += n
                if gi:
                    rec["later_gaps"] = rec.get("later_gaps", 0) + n
                if n > 1:
                    rec["block"] = rec.get("block", 0) + n
            rec.update(stop=reason, start_after=round(starts[j.id], 3))
            decisions.append(rec)
            if stopped:
                break

        errors = verify(inst, tasks, seqs, prec, starts, True)
        res.wall_time = res.solve_time = time.perf_counter() - t0
        res.objective_name = name
        if errors:
            res.status, res.error = "ERROR", "; ".join(errors)
            return res
        assigned = {t: c for c, s in seqs.items() for t in s}
        for t, q in tasks.items():
            st = starts[t]
            res.schedule.append(ScheduleEntry(
                task=t, crane=assigned[t], o=q.o, d=q.d, start=st, end=st + q.duration,
                duration=q.duration, kind="RH" if q.type == "RH" else "main",
                job_type=q.type, parent=q.parent, home=q.home, assisted=assigned[t] != q.home,
                ready=q.ready, truck_out=st + (p.pick if q.type in ("DS", "GI") else q.duration)))
        res.status = "CANCELLED" if nbrunner.cancelled() else "HEURISTIC_LIMIT" if stopped else "HEURISTIC"
        res.feasible = True
        res.objective = objective(inst, tasks, starts, name)
        counts = Counter(d["stop"] for d in decisions)
        res.metrics.update(
            n_rehandles=inst.num_rehandles(), constraint_verified=True,
            candidate_evaluations=trials, comparison_reference="coop",
            rh_after_arrival=rh_arr, home_rh_fixed=True, objective_guard=guard,
            gap_search=gap_search, recheck=recheck, ld_lookahead=ld_h,
            late_helps=len(self._late_helps(tasks, seqs, starts)),
            no_help_objective=round(val0, 4),
            stop_reasons={STOP_LABEL[k]: v for k, v in counts.items()},
            window_decisions=decisions)
        res.notes.append(("ver2 (빈틈 탐색 " + ("켬" if gap_search else "끔") + " · 사후 재검사 " + ("켬" if recheck else "끔") + "). ")
                         + "규칙: " + ("규칙 0 켬 (RH 는 트럭 도착 뒤). " if rh_arr else "규칙 0 끔 (RH 를 트럭 도착 전에도 가능). ")
                         + (f"적하 RH 는 AGV 도착 {ld_h / 60:.0f}분 전부터. " if ld_h is not None else "")
                         + ("규칙 1 켬. " if use_rule1 else "규칙 1 끔. ")
                         + ("목적값 보호 켬." if guard else "목적값 보호 끔 (규칙대로 도울 수 있으면 돕기)."))
        res.notes.append(f"무지원 스케줄 목적값 {val0:.1f} → {res.objective:.1f}. 판단 대상 본 작업 {len(decisions)}개: "
                         + ", ".join(f"{STOP_LABEL[k]} {v}" for k, v in counts.most_common()))
        for d in decisions:
            res.notes.append(f"  {d['job']} (원래 {d['home']}, rehandle {d['k']}개, 트럭 {d['a_j']:.0f}s, A_H {d['A_H']:.0f}s): "
                             f"{d['helper']} 가 {d['helped']}개 지원" + (f" (그중 {d['later_gaps']}개는 뒤 빈틈)" if d.get('later_gaps') else "") + f" → j 시작 {d['start_before']:.0f} → {d['start_after']:.0f}s · {STOP_LABEL[d['stop']]}")
        res.notes.append("본 작업 순서는 고정이라, 지원받는 동안 원래 크레인은 다음 작업으로 넘어가지 못한다. "
                         "'지원 겹침' 은 지원 rehandle 이 원래 크레인의 다른 작업과 동시에 진행된 시간, "
                         "'지원 대기' 는 원래 크레인이 rehandle 이 끝나기를 기다린 시간(추정).")
        print(f"[{self.key}] {res.status} objective={res.objective:.4f} (무지원 {val0:.4f}), trials={trials}, "
              + ", ".join(f"{k}={v}" for k, v in counts.items()))
        return res.finalize(t_hat=float(p.t_hat))


class WindowRuleV2(WindowRule):
    """ver2 = ver1 + 빈틈 탐색 + 사후 재검사 (2026-10-14).

    빈틈 탐색: 첫 자리(GO 트럭 도착 직후 지원 크레인의 첫 작업 앞)에서 규칙 3·4(나)에 걸리면 포기하지 않고,
               지원 크레인 자기 본 작업 바로 뒤 자리들(= 자기 작업 사이의 빈틈)을 시간 순서대로 시도해
               규칙을 지키는 첫 틈에 넣는다. j 가 시작한 뒤의 틈은 보지 않는다.
    사후 재검사: 넣을 때마다 이미 넣어 둔 지원 rehandle 이 지원 크레인 자기 트럭 도착 뒤로 밀려나지 않는지
               확인하고, 밀려나면 그 자리는 쓰지 않는다 (다음 틈을 시도).
    """
    key = "window_rule_v2"
    label = "Window–Slack 지원 규칙 ver2"
    short = "창 규칙 v2"
    gap_search_default = True
    recheck_default = True
    description = ("ver1 규칙(0~4)에 두 가지를 더함: ① 빈틈 탐색 — 첫 자리에서 지원 크레인 트럭이 오면 포기하지 않고 "
                   "지원 크레인 자기 작업 사이의 다음 빈틈들을 차례로 시도, ② 사후 재검사 — 새로 넣은 지원 때문에 "
                   "앞서 넣은 지원이 자기 트럭 도착 뒤로 밀리면 그 자리는 쓰지 않음.")
    options = {
        **WindowRule.options,
        "gap_search": {"type": "checkbox", "default": True, "label": "① 빈틈 탐색 (지원 크레인 다음 빈틈들 시도)"},
        "recheck": {"type": "checkbox", "default": True, "label": "② 사후 재검사 (앞서 넣은 지원이 밀리면 거절)"},
        "max_gaps": {"type": "number", "default": 12, "label": "rehandle 하나당 시도할 빈틈 수 (최대)"},
        "block_move": {"type": "checkbox", "default": False,
                       "label": "(실험용) 하나씩 안 되면 남은 rehandle 을 묶어서 한 틈에 넘겨 보기"},
    }
