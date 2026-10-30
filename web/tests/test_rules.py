"""Constraint/property tests; --mip also pins heuristic schedules in original MIPs."""
import argparse
import contextlib
import io
from pathlib import Path
import sys
import unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from app.schema import small_instance, default_instance, Instance, Job, Params
from app.generator import generate
from app.methods.rules import LonRemRule, RemReqRule, AssistRule, tasks_of, decode, verify, initial_schedule
from app.methods.window_rule import WindowRule, WindowRuleV2
from app.methods.baseline import BaselineRehandle
from app.methods.coop import CoopRehandle
from app.comparison import comparison_metrics
from app import nbrunner

MIP = "--mip" in sys.argv
if MIP: sys.argv.remove("--mip")

class RulesTests(unittest.TestCase):
    def solve(self,m,inst,**opts):
        with contextlib.redirect_stdout(io.StringIO()),contextlib.redirect_stderr(io.StringIO()):
            return m.solve(inst,opts)

    def test_random_feasibility(self):
        for seed in range(30):
            inst=generate(seed=seed,n_ds=2,n_ld=2,n_gi=2,n_go=3,rh_prob=.8,rh_move_prob=.7)
            for method in (LonRemRule(),RemReqRule(),AssistRule()):
                res=self.solve(method,inst,objective="truck_wait",passes=2)
                self.assertTrue(res.feasible,(seed,method.key,res.error))
                self.assertTrue(res.metrics["constraint_verified"])
                self.assertIsNone(res.mip_gap)
                self.assertIsNone(res.best_bound)
                if not method.split:
                    self.assertEqual(res.metrics["n_assisted"],0)

    def test_single_crane_fractional_empty_go(self):
        inst=Instance(seq_sc=[Job("DS_1","DS",8,a=70),Job("LD_1","LD",6,r_c=1,rh_d=[5])],
                      params=Params(t_hat=.4,pick=.7,drop=.3,rh_inplace=2.1))
        for name in ("makespan","truck_wait","go_wait"):
            for m in (LonRemRule(),RemReqRule(),AssistRule()):
                res=self.solve(m,inst,objective=name)
                self.assertTrue(res.feasible,res.error)
                self.assertGreaterEqual(res.objective,0)

    def test_insertion_does_not_worsen_and_no_delay(self):
        for seed in range(10):
            inst=generate(seed=seed,n_ds=2,n_ld=1,n_gi=1,n_go=3,rh_prob=1,sc_gap=60)
            tasks,seq,prec=tasks_of(inst,True)
            from app.methods.rules import objective
            for name in ("makespan","truck_wait","go_wait"):
                st=initial_schedule(inst,tasks,seq,prec,name,"lonrem")
                res=self.solve(AssistRule(),inst,objective=name,no_delay=True,rh_after_arrival=False)
                self.assertTrue(res.feasible,res.error)
                self.assertLessEqual(res.objective,objective(inst,tasks,st,name)+1e-7)
                for entry in res.schedule:
                    if entry.kind=="main" and entry.crane=="SC":
                        self.assertLessEqual(entry.start,st[entry.task]+1e-7)

    def test_window_rule(self):
        """창 규칙: 제약 통과, 규칙 3(지원 크레인 다음 트럭이 오기 전에만 지원), 목적값 보호, 지원 끄면 무지원과 같음."""
        for seed in range(25):
            inst=generate(seed=seed,n_ds=2,n_ld=2,n_gi=2,n_go=3,rh_prob=.8,rh_move_prob=.7,sc_gap=40,lc_gap=15)
            for arr in (True,False):
                res=self.solve(WindowRule(),inst,objective="truck_wait",rh_after_arrival=arr)
                self.assertTrue(res.feasible,(seed,res.error))
                ready={e.task:e.ready for e in res.schedule}
                for c in ("SC","LC"):
                    seq=sorted((e for e in res.schedule if e.crane==c),key=lambda e:e.start)
                    for k,e in enumerate(seq):
                        if e.kind=="RH" and e.assisted:
                            if arr: self.assertGreaterEqual(e.start+1e-7,ready[e.parent])
                            nxt=next((x for x in seq[k+1:] if x.kind=="main"),None)
                            if nxt is not None: self.assertGreater(nxt.ready,e.start-1e-7,(seed,e.task))
                g=self.solve(WindowRule(),inst,objective="truck_wait",objective_guard=True)
                self.assertLessEqual(g.objective,g.metrics["no_help_objective"]+1e-6)
                off=self.solve(WindowRule(),inst,objective="truck_wait",help_go=False,help_ld=False,rh_after_arrival=arr)
                self.assertEqual(off.metrics["n_assisted"],0)
                self.assertAlmostEqual(off.objective,off.metrics["no_help_objective"],places=6)

    def test_window_rule_v2(self):
        """ver2: 제약 통과, 사후 재검사로 '자기 트럭 기다리게 한 지원' 0, ver1 과 같은 옵션을 끄면 ver1 과 같은 해."""
        for seed in range(25):
            inst=generate(seed=seed,n_ds=2,n_ld=2,n_gi=2,n_go=3,rh_prob=.8,rh_move_prob=.7,sc_gap=40,lc_gap=15)
            v2=self.solve(WindowRuleV2(),inst,objective="truck_wait",draw_notebook_figure=False)
            self.assertTrue(v2.feasible,(seed,v2.error))
            self.assertEqual(v2.metrics["late_helps"],0,seed)
            off=self.solve(WindowRuleV2(),inst,objective="truck_wait",gap_search=False,recheck=False)
            v1=self.solve(WindowRule(),inst,objective="truck_wait")
            self.assertAlmostEqual(off.objective,v1.objective,places=6)
            blk=self.solve(WindowRuleV2(),inst,objective="truck_wait",block_move=True)
            self.assertTrue(blk.feasible,(seed,blk.error))

    def test_ld_lookahead(self):
        """적하 rehandle 사전 정보: LD RH 시작 가능 시각 = max(0, a−H), 반출은 그대로. 지원·제약 통과."""
        from app.generator import LD_PRESET
        pre={k:v for k,v in LD_PRESET.items() if k not in ("params","note")}
        for seed in range(12):
            inst=generate(seed=seed,params=Params(**LD_PRESET["params"]),**pre)
            for m in (WindowRule(),WindowRuleV2()):
                r=self.solve(m,inst,objective="truck_wait",ld_known=True,ld_lookahead_min=30,draw_notebook_figure=False)
                self.assertTrue(r.feasible,(seed,r.error))
                self.assertEqual(r.metrics["ld_lookahead"],1800)
                job={j.id:j for j in inst.seq_sc+inst.seq_lc}
                for e in r.schedule:
                    if e.kind=="RH":
                        a=inst.ready(job[e.parent])
                        want=max(0,a-1800) if job[e.parent].type=="LD" else a
                        self.assertAlmostEqual(e.ready,want,places=6)
                        self.assertGreaterEqual(e.start+1e-7,want)

    def test_crane_ready(self):
        """CRANE_READY: 크레인이 비는 시각 · 시작 위치를 주면 그 뒤에만 시작, 0 이면 예전과 같다."""
        inst=default_instance()
        base=self.solve(WindowRuleV2(),inst,objective="truck_wait",draw_notebook_figure=False)
        inst2=Instance.from_dict(inst.to_dict()); inst2.block.sc_ready=40.0; inst2.block.lc_ready=15.0
        r=self.solve(WindowRuleV2(),inst2,objective="truck_wait",draw_notebook_figure=False)
        self.assertTrue(r.feasible,r.error)
        for e in r.schedule:
            self.assertGreaterEqual(e.start+1e-7,{"SC":40.0,"LC":15.0}[e.crane])
        inst3=Instance.from_dict(inst.to_dict())
        self.assertAlmostEqual(self.solve(WindowRuleV2(),inst3,objective="truck_wait",draw_notebook_figure=False).objective,base.objective)

    def test_rolling(self):
        """롤링 호라이즌: (1) 정보를 처음부터 다 알면(H·ET 매우 큼) 정적 창 규칙 v2 와 같은 스케줄,
        (2) AGV 60분 전 · ET 도착 시 공개에서도 노트북 제약 통과, 반출 RH 는 도착 뒤, 적하 RH 는 a−H 뒤."""
        from app.generator import LD_PRESET
        from app.registry import build_registry
        reg=build_registry(str(Path(__file__).resolve().parents[2]/"model"))
        pre={k:v for k,v in LD_PRESET.items() if k not in ("params","note")}
        for seed in range(6):
            inst=generate(seed=seed,params=Params(**LD_PRESET["params"]),**pre)
            ro=reg["rolling_window_v2"]
            full=self.solve(WindowRuleV2(),inst,objective="truck_wait",rh_after_arrival=True,ld_known=True,
                            ld_lookahead_min=1e5,draw_notebook_figure=False)
            one=self.solve(ro,inst,objective="truck_wait",agv_info_min=1e5,et_info_min=1e5,draw_notebook_figure=False)
            self.assertEqual(one.metrics["replans"],1)
            self.assertAlmostEqual(one.objective,full.objective,places=6)
            for key in ("rolling_window_v2","rolling_nohelp"):
                r=self.solve(reg[key],inst,objective="truck_wait",agv_info_min=60,draw_notebook_figure=False)
                self.assertTrue(r.feasible,(seed,key,r.error))
                self.assertGreater(r.metrics["replans"],1)
                job={j.id:j for j in inst.seq_sc+inst.seq_lc}
                for e in r.schedule:
                    if e.kind=="RH":
                        a=inst.ready(job[e.parent])
                        want=max(0,a-3600) if job[e.parent].type=="LD" else a
                        self.assertGreaterEqual(e.start+1e-7,want)
                if key=="rolling_nohelp":
                    self.assertFalse(any(e.assisted for e in r.schedule))

    def test_instance_sets(self):
        """5시간 실험 세트: 시드 고정(두 번 만들어도 같음), 도착은 5시간 안, 부하 맞춘 것은 목표 ± 0.04, 균형 27 + 극단 16."""
        from app.instance_sets import build_set, stats, TOL, T5H
        a=build_set(); b=build_set()
        self.assertEqual([i.to_dict() for _,i in a],[i.to_dict() for _,i in b])
        self.assertEqual(sum(s["group"]=="균형" for s,_ in a),27)
        self.assertEqual(sum(s["group"]=="극단" for s,_ in a),16)
        self.assertEqual(len({i.id for _,i in a}),43)
        from app.schema import validate
        for s,i in a:
            self.assertFalse(validate(i)["errors"],i.name)
            self.assertLess(max(i.ready(j) for j in i.jobs()),T5H)
            if s["match"]:
                st=stats(i)
                self.assertLessEqual(abs(st["rho_sc"]-s["rho_sc"]),TOL+1e-9,i.name)
                self.assertLessEqual(abs(st["rho_lc"]-s["rho_lc"]),TOL+1e-9,i.name)

    def test_rolling_periodic(self):
        """일정 간격 투입: 첫 정보 시각부터 Δ분마다, 작업은 자기가 투입된 시점(정보 공개 뒤 첫 투입) 전에 시작하지 않음, 제약 통과."""
        from app.generator import LD_PRESET
        from app.registry import build_registry
        reg=build_registry(str(Path(__file__).resolve().parents[2]/"model"))
        pre={k:v for k,v in LD_PRESET.items() if k not in ("params","note")}
        for seed in range(4):
            inst=generate(seed=seed,params=Params(**LD_PRESET["params"]),**pre)
            for iv in (15,30):
                r=self.solve(reg["rolling_window_v2"],inst,objective="truck_wait",agv_info_min=60,
                             replan_mode="일정 간격",replan_interval_min=iv,draw_notebook_figure=False)
                self.assertTrue(r.feasible,(seed,iv,r.error))
                ts=r.metrics["replan_times"]
                self.assertTrue(all(abs((b-a)-iv*60)<1e-6 for a,b in zip(ts,ts[1:])))
                self.assertEqual(sum(r.metrics["replan_added"]),inst.num_jobs())
                job={j.id:j for j in inst.seq_sc+inst.seq_lc}
                for e in r.schedule:
                    jid=e.parent or e.task; j=job[jid]
                    rev=max(0,inst.ready(j)-(3600 if j.type in ("DS","LD") else 0))
                    rel=min(t for t in ts if t>=rev-1e-6)
                    self.assertGreaterEqual(e.start+1e-6,rel,(e.task,rev,rel))

    def test_instance_sets_10h(self):
        """10시간 세트: 균형 18 · 대규모 6 · 몰림 20, 도착은 10시간 안 · 크레인 순서대로, 평균 부하 목표 ± 0.04,
        몰림은 가장 바쁜 1시간 부하가 평균보다 확실히 큼, 대규모는 작업 200개 이상."""
        from app.instance_sets import build_set, stats, TOL, T10H
        from app.schema import validate
        a=build_set("rolling10h")
        g=lambda name: [x for x in a if x[0]["group"]==name]
        self.assertEqual((len(g("균형")),len(g("대규모")),len(g("몰림"))),(18,6,20))
        self.assertEqual(len({i.id for _,i in a}),44)
        for s,i in a:
            self.assertFalse(validate(i)["errors"],i.name)
            for seq in (i.seq_sc,i.seq_lc):
                arr=[i.ready(j) for j in seq]
                self.assertEqual(arr,sorted(arr),i.name)
                self.assertLess(arr[-1],T10H)
            st=stats(i)
            self.assertLessEqual(abs(st["rho_sc"]-s["rho_sc"]),TOL+1e-9,i.name)
            self.assertLessEqual(abs(st["rho_lc"]-s["rho_lc"]),TOL+1e-9,i.name)
            if s["group"]=="몰림":
                self.assertGreater(max(st["rho_peak_sc"],st["rho_peak_lc"]),1.3,i.name)
            if s["group"]=="대규모":
                self.assertGreaterEqual(i.num_jobs(),200,i.name)

    def test_instance_sets_loading(self):
        """적하 중심 세트: 적하 비중 18 · 적하 RH 6 · 선박 패턴 12, 적하 비중 맞음, 양하 후 적하 순서, 적하 장치 위치."""
        from app.instance_sets import build_set, stats, TOL, T10H
        from app.schema import validate
        a=build_set("loading10h")
        self.assertEqual(len(a),36)
        for s,i in a:
            self.assertFalse(validate(i)["errors"],i.name)
            st=stats(i)
            if "ld_share" in s:
                self.assertAlmostEqual(st["ld_share"],s["ld_share"],delta=0.02)
            if s["match"]:
                self.assertLessEqual(abs(st["rho_sc"]-s["rho_sc"]),TOL+1e-9,i.name)
            arr=[i.ready(j) for j in i.seq_sc]
            self.assertEqual(arr,sorted(arr)); self.assertLess(arr[-1],T10H)
            if s.get("sc_order")=="ds_then_ld":
                t="".join(j.type[0] for j in i.seq_sc)
                self.assertEqual(t,"D"*t.count("D")+"L"*t.count("L"))
            if s.get("gen",{}).get("ld_bay")=="land":
                import statistics
                self.assertGreater(statistics.median(j.bay for j in i.seq_sc if j.type=="LD"),i.block.n_bay*0.6)

    def test_generator_arrival_and_go_bay(self):
        """도착 방식(일정·포아송·몰림)과 GO bay 위치 옵션: 순서대로 도착, 평균 간격 유지, bay 범위."""
        import statistics
        gaps={"poisson":[], "burst":[]}
        for seed in range(60):
            for arr in ("poisson","burst"):
                inst=generate(seed=seed,n_ds=2,n_ld=2,n_gi=3,n_go=5,lc_gap=100,sc_gap=50,arrival=arr,burst_size=3)
                a=[j.a for j in inst.seq_lc]
                self.assertEqual(a,sorted(a)); self.assertEqual(a[0],0)
                gaps[arr].append(a[-1]/(len(a)-1))
                if arr=="burst": self.assertEqual(a[:3],[0,0,0])
        self.assertAlmostEqual(statistics.mean(gaps["poisson"]),100,delta=15)
        for gb,lo,hi in (("sea",1,30),("land",31,60)):
            bays=[j.bay for s in range(80) for j in generate(seed=s,n_bay=60,n_go=5,go_bay=gb).seq_lc if j.type=="GO"]
            self.assertTrue(all(1<=b<=60 for b in bays))
            self.assertTrue(lo<=statistics.median(bays)<=hi,(gb,statistics.median(bays)))

    def test_determinism_and_limit(self):
        inst=default_instance()
        a=self.solve(AssistRule(),inst,objective="truck_wait")
        b=self.solve(AssistRule(),inst,objective="truck_wait")
        self.assertEqual(a.schedule,b.schedule)
        r=self.solve(AssistRule(),inst,time_limit=.001)
        self.assertTrue(r.feasible)
        self.assertEqual(r.status,"HEURISTIC_LIMIT")
        nbrunner.request_cancel()
        try:
            r=self.solve(AssistRule(),inst)
            self.assertTrue(r.feasible)
            self.assertEqual(r.status,"CANCELLED")
        finally:
            nbrunner.clear_cancel()

    def test_broken_schedule_detected(self):
        inst=small_instance()
        for split in (False,True):
            tasks,seq,prec=tasks_of(inst,split)
            st={t:0 for t in tasks}
            self.assertTrue(verify(inst,tasks,seq,prec,st,split))

    def test_comparison_labels(self):
        from app.methods.base import MethodResult
        ref=MethodResult(method="baseline",label="MIP",feasible=True,status="TIME_LIMIT",objective=10,
                         best_bound=8,objective_name="makespan",wall_time=2)
        r=MethodResult(method="lonrem",label="rule",objective=12,objective_name="makespan",wall_time=.1,
                       metrics={"comparison_reference":"baseline"})
        cmp=comparison_metrics(r,{"baseline":ref})
        self.assertIsNone(cmp["rpd_pct"])
        self.assertEqual(cmp["incumbent_deviation_pct"],20)
        ref.status="OPTIMAL"
        self.assertEqual(comparison_metrics(r,{"baseline":ref})["rpd_pct"],20)

    @unittest.skipUnless(MIP,"use --mip to validate in original notebooks")
    def test_original_notebook_accepts_fixed_schedules(self):
        from app import nbrunner as nb
        import tempfile
        nbdir=str(Path(__file__).resolve().parents[2]/"model")
        for inst in (small_instance(),generate(seed=4,n_ds=1,n_ld=1,n_gi=1,n_go=2,rh_prob=1,rh_max=2)):
            for name in ("makespan","truck_wait","go_wait"):
                for rule in (LonRemRule(),RemReqRule(),AssistRule(),WindowRule(),WindowRuleV2()):
                    r=self.solve(rule,inst,objective=name)
                    split=getattr(rule,"split",True)
                    adapter=CoopRehandle(nbdir) if split else BaselineRehandle(nbdir)
                    opts=dict(objective=name,draw_notebook_figure=False,time_limit=10,threads=2)
                    if split: opts["rh_after_arrival"]=bool(r.metrics.get("rh_after_arrival"))
                    if split and r.metrics.get("ld_lookahead") is not None:
                        opts.update(ld_known=True, ld_lookahead_min=r.metrics["ld_lookahead"]/60)
                    with tempfile.NamedTemporaryFile(suffix=".log") as f,contextlib.redirect_stdout(io.StringIO()),contextlib.redirect_stderr(io.StringIO()):
                        cells=adapter.build_cells(inst,opts,f.name)
                        # Execute all model-building cells, excluding optimize/result cell.
                        outcome=nb.execute_cells(cells[:-1])
                        self.assertTrue(outcome.ok,outcome.error)
                        ns=outcome.namespace
                        model=ns["model"]
                        for e in r.schedule:
                            ns["s"][e.task].LB=ns["s"][e.task].UB=e.start
                            if e.kind=="RH":
                                for c in ns["cand"][e.task]:
                                    ns["x"][e.task,c].LB=ns["x"][e.task,c].UB=int(c==e.crane)
                        ns["makespan"].LB=ns["makespan"].UB=r.makespan
                        model.Params.OutputFlag=0
                        model.Params.TimeLimit=10
                        model.optimize()
                        self.assertGreater(model.SolCount,0,(inst.id,name,rule.key,model.Status))
                        self.assertAlmostEqual(model.ObjVal,r.objective,places=5)
                        model.dispose()
                    opt=self.solve(adapter,inst,**opts)
                    self.assertEqual(opt.status,"OPTIMAL",opt.error)
                    self.assertLessEqual(opt.objective,r.objective+1e-5)
        # 롤링 호라이즌 최종 스케줄도 노트북 제약 통과 (AGV 30분 전 · ET 도착 시 공개)
        from app.registry import build_registry
        reg=build_registry(nbdir)
        for seed in (3,4,9):
            inst=generate(seed=seed,n_ds=2,n_ld=2,n_gi=1,n_go=2,rh_prob=1,rh_max=2,sc_gap=60,lc_gap=80)
            for key in ("rolling_window_v2","rolling_coop"):
                r=self.solve(reg[key],inst,objective="truck_wait",agv_info_min=0.5,time_limit=10,threads=2,draw_notebook_figure=False)
                self.assertTrue(r.feasible,(seed,key,r.error))
                opts=dict(objective="truck_wait",draw_notebook_figure=False,time_limit=10,threads=2,
                          rh_after_arrival=True,ld_known=True,ld_lookahead_min=0.5)
                adapter=CoopRehandle(nbdir)
                with tempfile.NamedTemporaryFile(suffix=".log") as f,contextlib.redirect_stdout(io.StringIO()),contextlib.redirect_stderr(io.StringIO()):
                    outcome=nb.execute_cells(adapter.build_cells(inst,opts,f.name)[:-1])
                    self.assertTrue(outcome.ok,outcome.error)
                    ns=outcome.namespace; model=ns["model"]
                    for e in r.schedule:
                        ns["s"][e.task].LB=ns["s"][e.task].UB=e.start
                        if e.kind=="RH":
                            for c in ns["cand"][e.task]:
                                ns["x"][e.task,c].LB=ns["x"][e.task,c].UB=int(c==e.crane)
                    ns["makespan"].LB=ns["makespan"].UB=r.makespan
                    model.Params.OutputFlag=0; model.Params.TimeLimit=10
                    model.optimize()
                    self.assertGreater(model.SolCount,0,(seed,key,model.Status))
                    self.assertAlmostEqual(model.ObjVal,r.objective,places=5)
                    model.dispose()

if __name__=="__main__": unittest.main()
