#!/usr/bin/env python3
"""Run reproducible comparisons through the SAME web API and persist in its archive.
Example: python benchmark_rules.py --seed 42 --count 5 --toy --summary data/rules_summary.json
"""
import argparse
import json
import time
import urllib.request
from pathlib import Path
from statistics import mean


def main():
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--url",default="http://127.0.0.1:8766")
    ap.add_argument("--seed",type=int,default=42)
    ap.add_argument("--count",type=int,default=5)
    ap.add_argument("--toy",action="store_true")
    ap.add_argument("--objectives",nargs="+",choices=["makespan","truck_wait","go_wait"],default=["makespan","truck_wait","go_wait"])
    ap.add_argument("--mip-seconds",type=float,default=15)
    ap.add_argument("--summary",default="data/rules_summary.json")
    args=ap.parse_args()
    def api(path,payload=None):
        req=urllib.request.Request(args.url+path,data=None if payload is None else json.dumps(payload).encode(),
                                   headers={"Content-Type":"application/json"})
        with urllib.request.urlopen(req,timeout=30) as f: return json.load(f)
    cfg=api("/api/config")
    generation=dict(seed=args.seed,count=args.count,n_bay=20,n_ds=1,n_ld=1,n_gi=1,n_go=2,
                    rh_prob=.8,rh_max=2,rh_move_prob=.5,rh_move_span=4,sc_gap=10,lc_gap=10,save=False)
    instances=api("/api/generate",generation)["instances"]
    if args.toy: instances.append(cfg["default_instance"])
    methods=["baseline","coop","lonrem","remreq","assist_lonrem"]
    opt={k:dict(time_limit=args.mip_seconds,threads=2,mip_gap=0,draw_notebook_figure=False)
         for k in ["baseline","coop"]}
    opt.update({k:dict(time_limit=30,passes=3,go_only=True,no_delay=False) for k in methods[2:]})
    records=[]
    for index,inst in enumerate(instances):
        for name in args.objectives:
            seed=args.seed+index if index<args.count else "toy"
            rid=api("/api/run",dict(instance=inst,methods=methods,options=opt,objective=name,
                    label=f"Rule pilot v2 seed={seed} / {name}",owner="Codex experiment",save_result=True,save_instance=True))["run_id"]
            deadline=time.monotonic()+2*args.mip_seconds+120
            while True:
                snap=api("/api/jobs/"+rid)
                if snap["status"] in ("done","error","cancelled"): break
                if time.monotonic()>deadline: raise RuntimeError("run timeout: "+rid)
                time.sleep(.25)
            results=snap["results"]
            records.append(dict(run_id=rid,seed=seed,objective=name,instance_id=snap.get("instance_id"),
                                instance=inst,options=opt,results=results))
            print(f"{len(records)}/{len(instances)*len(args.objectives)} {rid} {name}: "+
                  ", ".join(f"{r['method']}={r.get('objective')} ({r['status']})" for r in results),flush=True)
            if any(not r.get("feasible") for r in results):
                raise RuntimeError("method failed: "+rid)
    summary=[]
    for name in args.objectives:
        for k in methods:
            rs=[r for rec in records if rec["objective"]==name and rec["seed"]!="toy" for r in rec["results"] if r["method"]==k]
            rpds=[r["metrics"].get("rpd_pct") for r in rs if r["metrics"].get("rpd_pct") is not None]
            summary.append(dict(objective=name,method=k,n=len(rs),optimal_reference_n=len(rpds),
                          mean_rpd_pct=mean(rpds) if rpds else None,
                          mean_wall_seconds=mean(r["wall_time"] for r in rs) if rs else None))
    out=Path(args.summary);out.parent.mkdir(parents=True,exist_ok=True)
    out.write_text(json.dumps(dict(generation=generation,mip_seconds=args.mip_seconds,
                                  summary=summary,records=records),ensure_ascii=False,indent=2))
    print("summary:",out.resolve(),flush=True)
    print(json.dumps(summary,ensure_ascii=False,indent=2))

if __name__=="__main__": main()
