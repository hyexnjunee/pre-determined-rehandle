"""Solver-free priority rules and greedy RH insertion under notebook ETD constraints.

These are adaptations of Carlo & Martínez-Acevedo (2015), not reproductions of
its exchange-point simulator or of its unspecified two-rule combination.
"""
from __future__ import annotations

from dataclasses import dataclass
import time
from ..schema import Instance, validate
from .. import nbrunner
from .base import Method, ScheduleEntry

EPS = 1e-7


def init_pos(inst: Instance, crane: str) -> float:
    """크레인 시작 위치. 인스턴스에 sc_init / lc_init 이 있으면 그것 (실데이터), 없으면 TP."""
    b = inst.block
    if crane == "SC":
        return 0 if b.sc_init is None else float(b.sc_init)
    return b.n_bay + 1 if b.lc_init is None else float(b.lc_init)

def crane_ready(inst: Instance, crane: str) -> float:
    """크레인이 비는 시각 (롤링 호라이즌용, 노트북 CRANE_READY). 보통 0."""
    b = inst.block
    return float((b.sc_ready if crane == "SC" else b.lc_ready) or 0.0)


def first_start(inst: Instance, crane: str, o: float) -> float:
    """크레인이 작업 시작점 o 에 가장 빨리 닿는 시각 = 비는 시각 + 시작 위치에서 이동 (노트북 제약 (4))."""
    return crane_ready(inst, crane) + abs(init_pos(inst, crane) - o) * inst.params.t_hat


@dataclass(frozen=True)
class Task:
    id: str
    home: str
    o: int
    d: int
    duration: float
    ready: float
    type: str
    parent: str = ""
    rh_time: float = 0
    lo: int = 0
    hi: int = 0


def tasks_of(inst: Instance, split: bool, rh_after_arrival: bool = False, ld_lookahead: float | None = None):
    """rh_after_arrival=True 면 RH 시작 가능 시각 = 그 본 작업의 트럭 도착 a (노트북 RH_AFTER_ARRIVAL).
    ld_lookahead=H(초) 면 적하(LD) RH 는 max(0, a − H) (노트북 LD_LOOKAHEAD, AGV 도착을 H 전부터 앎)."""

    def rh_ready(job):
        if job.type == "LD" and ld_lookahead is not None:
            return max(0.0, inst.ready(job) - float(ld_lookahead))
        return inst.ready(job) if rh_after_arrival else 0
    p, land = inst.params, inst.block.n_bay + 1
    tasks, seqs, prec = {}, {"SC": [], "LC": []}, []
    for crane, jobs in (("SC", inst.seq_sc), ("LC", inst.seq_lc)):
        for job in jobs:
            o, d = ((0, job.bay) if job.type == "DS" else
                    (land, job.bay) if job.type == "GI" else
                    (job.bay, 0) if job.type == "LD" else (job.bay, land))
            dests = job.rh_d or [job.bay] * job.r_c
            rh_time = 0.
            last = None
            for k, dest in enumerate(dests, 1):
                du = p.rh_inplace if dest == job.bay else abs(dest-job.bay)*p.t_hat+p.pick+p.drop
                rh_time += du + abs(dest-job.bay)*p.t_hat
                if split:
                    rid = f"{job.id}_r{k}"
                    tasks[rid] = Task(rid, crane, job.bay, dest, du, rh_ready(job), "RH", job.id,
                                      lo=min(job.bay,dest), hi=max(job.bay,dest))
                    seqs[crane].append(rid)
                    if last:
                        prec.append((last, rid))
                    last = rid
            if last:
                prec.append((last, job.id))
            pts = [o,d] + ([] if split else dests)
            tasks[job.id] = Task(job.id, crane, o, d,
                                 abs(o-d)*p.t_hat+p.pick+p.drop+(0 if split else rh_time),
                                 inst.ready(job), job.type, rh_time=0 if split else rh_time,
                                 lo=min(pts), hi=max(pts))
            seqs[crane].append(job.id)
    return tasks, seqs, prec


def etd(i: Task, j: Task, crane: str, p):
    """Exact directed ETD formula from both original notebooks."""
    delta = p.safety_margin + 1
    if crane == "SC":
        base = (i.d + delta - j.o)*p.t_hat
        first = i.d + delta > j.o
        other = i.o + delta > j.o or i.d + delta > j.d
    else:
        base = (j.o - i.d + delta)*p.t_hat
        first = i.d - delta < j.o
        other = i.o - delta < j.o or i.d - delta < j.d
    return base if first else base-p.pick if other else base-2*p.pick


def conflict(i: Task, j: Task, crane: str, p):
    return (i.hi + p.safety_margin + 1 > j.lo if crane == "SC"
            else i.lo - p.safety_margin - 1 < j.hi)


def decode(inst, tasks, seqs, prec, rule="lonrem"):
    """Serial schedule generation with dynamic priorities for conflicting heads.

The global insertion order orients each cross-crane ETD disjunction. Starts
may overlap when permitted by ETD. All RH precedence and crane sequences are
respected. No Gurobi or MIP incumbent is used.
"""
    p = inst.params
    assigned = {t:c for c,seq in seqs.items() for t in seq}
    preds = {t:set() for t in tasks}
    for seq in seqs.values():
        for a,b in zip(seq,seq[1:]):
            preds[b].add(a)
    for a,b in prec:
        preds[b].add(a)
    starts, order = {}, []
    while len(order) < len(tasks):
        ready = [t for t in tasks if t not in starts and preds[t] <= starts.keys()]
        if not ready:
            return None  # insertion caused a precedence cycle
        earliest = {}
        for tid in ready:
            t, c = tasks[tid], assigned[tid]
            st = max(t.ready, first_start(inst, c, t.o))
            for prev in order:
                q, qc = tasks[prev], assigned[prev]
                if qc == c:
                    st = max(st, starts[prev]+q.duration+abs(q.d-t.o)*p.t_hat)
                elif conflict(q,t,qc,p):
                    st = max(st, starts[prev]+q.duration+etd(q,t,qc,p))
            for prev in preds[tid]:
                st = max(st, starts[prev]+tasks[prev].duration)
            earliest[tid] = st
        ready.sort(key=lambda t:(earliest[t], assigned[t]!="SC", t))
        chosen = ready[0]
        if len(ready) == 2:
            a,b = ready
            ca = assigned[a]
            ta,tb = tasks[a],tasks[b]
            if conflict(ta,tb,ca,p):
                # Only invoke the priority rule if the two earliest starts violate both orientations.
                ab = earliest[b]+EPS >= earliest[a]+ta.duration+etd(ta,tb,ca,p)
                ba = earliest[a]+EPS >= earliest[b]+tb.duration+etd(tb,ta,assigned[b],p)
                if not ab and not ba:
                    scores = {}
                    for t in ready:
                        remaining = [x for x in seqs[assigned[t]] if x not in starts]
                        scores[t] = (len(remaining) if rule=="remreq" else
                            sum(tasks[x].duration for x in remaining) +
                            sum(abs(tasks[x].d-tasks[y].o)*p.t_hat for x,y in zip(remaining,remaining[1:])) +
                            abs((tasks[seqs[assigned[t]][seqs[assigned[t]].index(t)-1]].d
                                 if seqs[assigned[t]].index(t) else
                                 init_pos(inst, assigned[t]))-tasks[t].o)*p.t_hat)
                    chosen = max(ready,key=lambda t:(scores[t], assigned[t]=="SC"))
        starts[chosen] = earliest[chosen]
        order.append(chosen)
    return starts


def objective(inst, tasks, starts, name):
    span = max(starts[t]+q.duration for t,q in tasks.items())
    if name == "makespan":
        return span
    return sum(starts[t]+(inst.params.pick if q.type in ("DS","GI") else q.duration)-q.ready
               for t,q in tasks.items() if q.type!="RH" and (name=="truck_wait" or q.type=="GO")) + .001*span


def verify(inst, tasks, seqs, prec, starts, split):
    """Check actual notebook constraints, independently of decoder's order."""
    errors, p = [], inst.params
    assigned = {t:c for c,seq in seqs.items() for t in seq}
    if set(assigned)!=set(tasks) or sum(map(len,seqs.values()))!=len(tasks):
        return ["assignment coverage"]
    horizon = sum(q.duration for q in tasks.values())+max(q.ready for q in tasks.values())+len(tasks)*(inst.block.n_bay+1)*p.t_hat
    for t,q in tasks.items():
        c = assigned[t]
        if starts[t]+EPS < max(q.ready, first_start(inst, c, q.o)):
            errors.append(f"release/initial {t}")
        if starts[t]>horizon+EPS:
            errors.append(f"horizon {t}")
    for a,b in prec:
        if starts[b]+EPS < starts[a]+tasks[a].duration:
            errors.append(f"precedence {a}/{b}")
    for c,seq in seqs.items():
        for k,a in enumerate(seq):
            for b in seq[k+1:]:
                if starts[b]+EPS < starts[a]+tasks[a].duration+abs(tasks[a].d-tasks[b].o)*p.t_hat:
                    errors.append(f"crane travel {a}/{b}")
        mains = [t for t in seq if tasks[t].type!="RH"]
        expected = [j.id for j in (inst.seq_sc if c=="SC" else inst.seq_lc)]
        if mains != expected:
            errors.append(f"main sequence {c}")
        if split:
            for r in seq:
                q = tasks[r]
                if q.type=="RH" and q.home==c:
                    k = expected.index(q.parent)
                    if k and starts[r]+EPS < starts[expected[k-1]]+tasks[expected[k-1]].duration+abs(tasks[expected[k-1]].d-q.o)*p.t_hat:
                        errors.append(f"HOME_RH_FIXED {r}")
    for a in seqs["SC"]:
        for b in seqs["LC"]:
            ta,tb = tasks[a],tasks[b]
            if conflict(ta,tb,"SC",p):
                if (starts[b]+EPS < starts[a]+ta.duration+etd(ta,tb,"SC",p) and
                    starts[a]+EPS < starts[b]+tb.duration+etd(tb,ta,"LC",p)):
                    errors.append(f"ETD {a}/{b}")
    return errors


def initial_schedule(inst, tasks, seqs, prec, name, rule):
    starts = decode(inst, tasks, seqs, prec, rule)
    # Keep a verified no-assistance baseline-rule embedding as an alternative.
    # Splitting RH changes the priority workload calculation, which can otherwise
    # produce a worse starting point even before any assistance is selected.
    base_tasks, base_seqs, base_prec = tasks_of(inst, False)
    base_starts = decode(inst, base_tasks, base_seqs, base_prec, rule)
    embedded = {}
    for jobs in (inst.seq_sc, inst.seq_lc):
        for j in jobs:
            at = base_starts[j.id]
            for k,d in enumerate(j.rh_d or [j.bay]*j.r_c, 1):
                rid = f"{j.id}_r{k}"
                embedded[rid] = at
                at += tasks[rid].duration + abs(d-j.bay)*inst.params.t_hat
            embedded[j.id] = at
    # Baseline's aggregated ETD is not always equivalent to split RH ETD.
    # Never accept this embedding unless ALL split-model constraints pass.
    if not verify(inst, tasks, seqs, prec, embedded, True):
        if starts is None or objective(inst,tasks,embedded,name) < objective(inst,tasks,starts,name)-EPS:
            return embedded
    return starts


class PriorityRule(Method):
    solver = "Python (solver-free)"
    role = "heuristic"
    paper = "Carlo & Martínez-Acevedo (2015), DOI 10.1016/j.cie.2015.04.026 — adapted ETD decoder"
    split = False
    rule = "lonrem"
    options = {"time_limit": {"type":"number", "default":30, "label":"휴리스틱 탐색 제한 (s)"},
               "draw_notebook_figure": {"type":"checkbox", "default":True, "label":"노트북 원본 차트 그리기"}}

    def solve(self, inst, options, context=None):
        t0 = time.perf_counter()
        res = self._new_result()
        invalid = validate(inst)["errors"]
        if invalid:
            res.status, res.error = "ERROR", "; ".join(invalid)
            return res
        name = options.get("objective", "truck_wait")
        if name not in ("makespan","truck_wait","go_wait"):
            raise ValueError("invalid objective")
        rh_arr = bool(options.get("rh_after_arrival", True)) if self.split else False
        tasks, seqs, prec = tasks_of(inst,self.split,rh_arr)
        starts = (initial_schedule(inst,tasks,seqs,prec,name,self.rule) if self.split
                  else decode(inst,tasks,seqs,prec,self.rule))
        if starts is None:
            raise RuntimeError("initial precedence cycle")
        trials, accepted, stopped = 0,0,False
        initial_sc = {t:starts[t] for t,q in tasks.items() if q.type!="RH" and q.home=="SC"}
        limit = max(.001, float(options.get("time_limit",30)))
        val = objective(inst,tasks,starts,name)
        if self.split:
            for _ in range(max(0,min(100,int(options.get("passes",3))))):
                best = None
                for rid,q in tasks.items():
                    if q.type!="RH" or (options.get("go_only",True) and tasks[q.parent].type!="GO"):
                        continue
                    source = next(c for c,s in seqs.items() if rid in s)
                    for target in ("SC","LC"):
                        stripped = {c:[t for t in s if t!=rid] for c,s in seqs.items()}
                        for slot in range(len(stripped[target])+1):
                            if nbrunner.cancelled() or time.perf_counter()-t0 >= limit:
                                stopped = True
                                break
                            candidate = {c:list(s) for c,s in stripped.items()}
                            candidate[target].insert(slot,rid)
                            if candidate == seqs:
                                continue
                            # Home RH must lie after previous main and before its own main.
                            if target == q.home:
                                mains=[j.id for j in (inst.seq_sc if target=="SC" else inst.seq_lc)]
                                idx=mains.index(q.parent)
                                pos=candidate[target].index(rid)
                                if pos>=candidate[target].index(q.parent) or (idx and pos<=candidate[target].index(mains[idx-1])):
                                    continue
                            trial=decode(inst,tasks,candidate,prec,self.rule)
                            trials+=1
                            if trial is None or verify(inst,tasks,candidate,prec,trial,True):
                                continue
                            if options.get("no_delay",False) and any(trial[t]>st+EPS for t,st in initial_sc.items()):
                                continue
                            value=objective(inst,tasks,trial,name)
                            if value < val-EPS and (best is None or value < best[0]-EPS):
                                best=(value,candidate,trial,rid,source,target,slot)
                        if stopped: break
                    if stopped: break
                if best:
                    val,seqs,starts,rid,source,target,slot=best
                    accepted+=1
                    print(f"[{self.key}] move {rid} {source}->{target} slot={slot}, objective={val:.6f}")
                if stopped or best is None: break
        errors=verify(inst,tasks,seqs,prec,starts,self.split)
        res.wall_time=res.solve_time=time.perf_counter()-t0
        res.objective_name=name
        if errors:
            res.status,res.error="ERROR","; ".join(errors)
            return res
        assigned={t:c for c,s in seqs.items() for t in s}
        for t,q in tasks.items():
            st=starts[t]
            res.schedule.append(ScheduleEntry(task=t,crane=assigned[t],o=q.o,d=q.d,
                start=st,end=st+q.duration,duration=q.duration,kind="RH" if q.type=="RH" else "main",
                job_type=q.type,parent=q.parent,home=q.home,assisted=assigned[t]!=q.home,
                rh_time=q.rh_time,ready=q.ready,
                truck_out=st+(inst.params.pick if q.type in ("DS","GI") else q.duration)))
        res.status="CANCELLED" if nbrunner.cancelled() else "HEURISTIC_LIMIT" if stopped else "HEURISTIC"
        res.feasible=True
        res.objective=objective(inst,tasks,starts,name)
        res.metrics.update(n_rehandles=inst.num_rehandles(),constraint_verified=True,
                           candidate_evaluations=trials,accepted_moves=accepted,
                           comparison_reference="coop" if self.split else "baseline",
                           home_rh_fixed=True if self.split else None,
                           go_only=bool(options.get("go_only",True)) if self.split else None,
                           no_delay=bool(options.get("no_delay",False)) if self.split else None,
                           rh_after_arrival=rh_arr if self.split else None)
        res.notes.append("논문 priority 아이디어를 노트북 ETD 제약에 적용한 변형. 논문 시뮬레이터/두 룰 조합의 재현이 아닙니다.")
        res.notes.append("MIP 해를 사용하지 않음. 시간창·초기 이동·고정 본 작업 순서·빈 이동·RH 선행·ETD 검증 통과. gap/bound 는 최적화 인증이 없어 비워 둠.")
        if self.split:
            res.notes.append("초기해는 독립 RH LonRem 스케줄과 baseline LonRem을 RH로 분해한 스케줄 중 목적값이 좋은 제약 검증 해를 선택. MIP를 사용하지 않음.")
            res.notes.append("GO-only 기본: GO RH만 재배치, LD RH는 원래 크레인. no-delay 선택 시 초기 무지원 LonRem 스케줄의 모든 SC 본 작업 시작보다 늦어지는 후보 제외.")
            res.notes.append("원래 크레인 RH는 직전 본 작업 이후. " + ("RH 는 트럭 도착 뒤에만 시작 (협업 노트북 RH_AFTER_ARRIVAL=True 와 같은 조건)." if rh_arr else
                             "RH release=0 (트럭 도착 전에도 가능, 협업 노트북 RH_AFTER_ARRIVAL=False 와 같은 조건)."))
        print(f"[{self.key}] {res.status} objective={res.objective:.6f}, trials={trials}, accepted={accepted}")
        return res.finalize(t_hat=float(inst.params.t_hat))

class LonRemRule(PriorityRule):
    key,label,short="lonrem","LonRem (지원 없음 · ETD)","LonRem"
    description="남은 처리시간 + 빈 이동시간이 큰 크레인 우선. RH는 baseline과 동일하게 본 작업 시간에 포함. 동률 SC 우선은 구현 선택."

class RemReqRule(PriorityRule):
    key,label,short="remreq","RemReq (지원 없음 · ETD)","RemReq"
    rule="remreq"
    description="남은 본 작업 수가 많은 크레인 우선. RH는 본 작업 시간에 포함. baseline MIP와 같은 문제의 빠른 비교군."

class AssistRule(PriorityRule):
    key,label,short="assist_lonrem","RH 삽입 + LonRem (협업 휴리스틱)","RH+LonRem"
    split=True
    description="무지원 스케줄에서 RH를 이동/재삽입. 후보 전체를 LonRem+ETD로 평가해 동일 목적값이 개선된 경우만 채택. 기본 GO→SC 지원, 선택적 SC 무지연 필터."
    options={**PriorityRule.options,
        "passes":{"type":"number","default":3,"label":"최대 개선 횟수"},
        "go_only":{"type":"checkbox","default":True,"label":"GO RH만 재할당 (끄면 LD도 허용)"},
        "no_delay":{"type":"checkbox","default":False,"label":"SC 본 작업 무지연 (초기 무지원 룰 스케줄 기준)"},
        "rh_after_arrival":{"type":"checkbox","default":True,"label":"rehandle 은 트럭 도착 뒤에만 시작 (RH_AFTER_ARRIVAL)"}}
