"""Quality metrics against a same-problem MIP, separate from operational improvement."""
def comparison_metrics(result, results):
    m = result.metrics
    key = m.get("comparison_reference")
    ref = results.get(key)
    if not ref or not ref.feasible or result.objective is None or ref.objective is None:
        return {}
    if key == "coop" and ref.metrics.get("allow_assist") is False and m.get("n_assisted", 0):
        return {}
    if result.objective_name != ref.objective_name:
        return {}
    la, lb = m.get("ld_lookahead"), ref.metrics.get("ld_lookahead")
    if ("ld_lookahead" in m or "ld_lookahead" in ref.metrics) and la != lb:
        return {"comparison_note": f"적하 rehandle 사전 정보가 다름 (이 방법 LD_LOOKAHEAD={la}, {key} LD_LOOKAHEAD={lb}) "
                                   "— MIP 대비 값을 계산하지 않음"}
    ra, rb = m.get("rh_after_arrival"), ref.metrics.get("rh_after_arrival")
    if ra is not None and rb is not None and bool(ra) != bool(rb):
        return {"comparison_note": f"rehandle 시작 조건이 다름 (이 방법 RH_AFTER_ARRIVAL={bool(ra)}, "
                                   f"{key} RH_AFTER_ARRIVAL={bool(rb)}) — MIP 대비 값을 계산하지 않음"}
    diff = result.objective-ref.objective
    deviation = 100*diff/abs(ref.objective) if abs(ref.objective)>1e-9 else (0. if abs(diff)<1e-9 else None)
    bound = ref.best_bound
    bound_gap = (100*(result.objective-bound)/abs(bound) if bound is not None and abs(bound)>1e-9 else None)
    return {"reference_method":key, "reference_status":ref.status,
            "reference_objective":ref.objective,
            "rpd_pct":deviation if ref.status=="OPTIMAL" and (ref.mip_gap or 0) <= 1e-7 else None,
            "incumbent_deviation_pct":deviation,
            "bound_gap_pct":bound_gap,
            "wall_speedup":ref.wall_time/result.wall_time if result.wall_time>0 else None}
