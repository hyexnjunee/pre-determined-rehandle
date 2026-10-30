"""
노트북 실행 어댑터 (공통)
=========================

model/ 폴더의 노트북을 원본 그대로 실행하되 아래 지점만 통합 인스턴스로 갈아끼운다.

  데이터 셀    `N_BAY`, `Seq_SC = {...}`, `Seq_LC = {...}`
  파라미터 셀  `safety_margin`, `t_hat`, `pick`, `drop`, `rh_inplace`, `OBJ`
               (+ 어댑터별 스위치: ALLOW_ASSIST, HOME_RH_FIXED)
  솔브 셀      `model.Params.TimeLimit`, OutputFlag, MIPGap, Threads, LogFile, 취소 콜백
  시각화 셀    `crane_colors = {` 가 있는 셀 → soft 셀 (실패해도 해는 유지)

노트북에서 **이 이름들만 바꾸지 않으면** 웹이 그대로 따라간다.
"""

from __future__ import annotations

import os
import re
import tempfile
import time
import traceback
from typing import Any

from .. import limits
from .. import livelog
from .. import nbrunner as nb
from ..schema import Instance, Job
from .base import Method, MethodResult

GRB_STATUS = {
    1: "LOADED", 2: "OPTIMAL", 3: "INFEASIBLE", 4: "INF_OR_UNBD", 5: "UNBOUNDED",
    6: "CUTOFF", 7: "ITERATION_LIMIT", 8: "NODE_LIMIT", 9: "TIME_LIMIT",
    10: "SOLUTION_LIMIT", 11: "INTERRUPTED", 12: "NUMERIC", 13: "SUBOPTIMAL",
    14: "INPROGRESS", 15: "USER_OBJ_LIMIT",
}

OBJECTIVES = ("makespan", "truck_wait", "go_wait")

COMMON_OPTIONS = {
    "time_limit": {"type": "number", "default": 300, "label": "Time limit (s)"},
    "mip_gap": {"type": "number", "default": 0.0, "label": "MIP gap"},
    "threads": {"type": "number", "default": limits.DEFAULT_THREADS,
                "label": f"Threads (최대 {limits.MAX_THREADS})"},
    "mem_limit_gb": {"type": "number", "default": limits.DEFAULT_MEM_GB,
                     "label": "메모리 soft limit (GB)"},
    "draw_notebook_figure": {"type": "checkbox", "default": True,
                             "label": "노트북 원본 차트 그리기"},
}


def num(value) -> str:
    """정수로 떨어지면 int 로 주입 (노트북 시각화가 range() 에 그대로 넣으므로)."""
    try:
        f = float(value)
    except (TypeError, ValueError):
        return repr(value)
    return repr(int(f)) if f.is_integer() else repr(f)


def seq_literal(name: str, jobs: list[Job], inst: Instance) -> str:
    if not jobs:
        return f"{name} = {{}}"
    lines = [f"{name} = {{"]
    for j in jobs:
        parts = [f"'type': {j.type!r}", f"'bay': {int(j.bay)}"]
        if j.r_c:
            parts.append(f"'r_c': {int(j.r_c)}")
            if j.rh_d and len(j.rh_d) == j.r_c:
                parts.append(f"'rh_d': {[int(v) for v in j.rh_d]!r}")
        parts.append(f"'a': {num(inst.ready(j))}")
        lines.append(f"    {j.id!r}: {{{', '.join(parts)}}},")
    lines.append("}")
    return "\n".join(lines)


class NotebookModel(Method):
    notebook: str = ""
    solver = "Gurobi"

    def __init__(self, notebook_dir: str):
        self.notebook_path = os.path.join(notebook_dir, self.notebook)

    # ------------------------------------------------------------------
    def available(self) -> tuple[bool, str]:
        if not os.path.exists(self.notebook_path):
            return False, f"노트북이 없습니다: {self.notebook_path}"
        try:
            import gurobipy  # noqa: F401
        except Exception as exc:
            return False, f"gurobipy 를 불러올 수 없습니다 ({exc})"
        return True, ""

    def switches(self, options: dict[str, Any]) -> dict[str, str]:
        """어댑터별로 파라미터 셀에 덮어쓸 스위치 {이름: 파이썬 코드}."""
        return {}

    # ------------------------------------------------------------------
    def build_cells(self, inst: Instance, options: dict[str, Any], log_file: str) -> list:
        cells = nb.load_code_cells(self.notebook_path)

        # 1) 데이터 셀
        i = nb.find_cell(cells, "Seq_SC = {")
        src = cells[i]
        src = nb.replace_assignment(src, "N_BAY", str(int(inst.block.n_bay)))
        src = nb.replace_dict_literal(src, "Seq_SC", seq_literal("Seq_SC", inst.seq_sc, inst))
        src = nb.replace_dict_literal(src, "Seq_LC", seq_literal("Seq_LC", inst.seq_lc, inst))
        # 크레인 시작 위치 (실데이터 인스턴스는 기록상 위치, 아니면 TP)
        b = inst.block
        if b.sc_init is not None or b.lc_init is not None:
            sc0 = "SEA_TP" if b.sc_init is None else num(b.sc_init)
            lc0 = "LAND_TP" if b.lc_init is None else num(b.lc_init)
            src = nb.replace_dict_literal(src, "Crane", "\n".join([
                "Crane = {", f'    "SC": {{"initial": {sc0}}},', f'    "LC": {{"initial": {lc0}}},', "}"]))
        cells[i] = src

        # 2) 파라미터 셀
        p = inst.params
        i = nb.find_cell(cells, "rh_inplace =")
        src = cells[i]
        obj = options.get("objective", "makespan")
        if obj not in OBJECTIVES:
            obj = "makespan"
        values = {
            "safety_margin": num(p.safety_margin), "t_hat": num(p.t_hat),
            "pick": num(p.pick), "drop": num(p.drop), "rh_inplace": num(p.rh_inplace),
            "OBJ": repr(obj),
        }
        values["CRANE_READY"] = repr({"SC": float(b.sc_ready or 0.0), "LC": float(b.lc_ready or 0.0)})
        values.update(self.switches(options))
        for name, code in values.items():
            if not re.search(rf"^{re.escape(name)}\s*=", src, re.MULTILINE):
                raise KeyError(f"노트북 파라미터 셀에서 '{name} =' 를 찾지 못했습니다.")
            src = nb.replace_assignment(src, name, code)
        cells[i] = src

        # 3) 솔브 셀 (결과 출력까지 포함)
        i = nb.find_cell(cells, "model.Params.TimeLimit")
        solve = cells[i]
        solve = nb.replace_attr_assignment(solve, "model.Params.TimeLimit",
                                           repr(float(options.get("time_limit", 300))))
        solve = nb.replace_attr_assignment(solve, "model.Params.OutputFlag", "1")
        extra = [
            f"model.Params.MIPGap = {float(options.get('mip_gap', 0.0) or 0.0)!r}",
            f"model.Params.LogFile = {log_file!r}",
            # 화면 로그는 LogFile 을 실시간으로 읽어 붙인다. gurobipy 는 콘솔 출력도 파이썬 stdout 으로 내보내므로
            # 그대로 두면 같은 줄이 두 번씩 찍힌다 → 콘솔 출력은 끔
            "model.Params.LogToConsole = 0",
        ]
        threads = int(options.get("threads", limits.DEFAULT_THREADS) or limits.DEFAULT_THREADS)
        extra.append(f"model.Params.Threads = {min(threads, limits.MAX_THREADS)}")
        mem_gb = float(options.get("mem_limit_gb", 0) or 0)
        if mem_gb > 0:
            extra.append(
                "try:\n"
                f"    model.Params.SoftMemLimit = {min(mem_gb, limits.MAX_MEM_GB)!r}\n"
                "except Exception as _e:\n"
                "    print('[rehandle-web] SoftMemLimit 미지원:', _e)")
        extra.append(
            "def _cranebench_cb(_m, _where):\n"
            "    try:\n"
            "        if _cranebench_cancelled():\n"
            "            _m.terminate()\n"
            "    except Exception:\n"
            "        pass")
        if "model.optimize()" not in solve:
            raise KeyError("솔브 셀에서 model.optimize() 를 찾지 못했습니다.")
        solve = solve.replace("model.optimize()",
                              "\n".join(extra) + "\nmodel.optimize(_cranebench_cb)", 1)

        out: list = cells[:i] + [nb.NBCell(solve, label="solve")]

        # 4) 시각화 셀 (matplotlib 을 못 쓰는 환경이면 건너뛴다)
        rest = cells[i + 1:]
        self._mpl_note = ""
        mpl_ok, mpl_err = nb.matplotlib_status()
        if options.get("draw_notebook_figure", True) and not mpl_ok:
            self._mpl_note = (
                "matplotlib 을 불러올 수 없어 노트북 원본 차트를 건너뛰었습니다 (해에는 영향 없음). "
                f"원인: {mpl_err}  → 서버를 띄운 파이썬에서 `pip install -U matplotlib` "
                "또는 Jupyter 커널과 같은 파이썬으로 run_server.py 를 실행하세요.")
            print("[rehandle-web] " + self._mpl_note)
        elif options.get("draw_notebook_figure", True):
            for src in rest:
                if "crane_colors = {" in src:
                    out.append(nb.NBCell(src, soft=True, label="notebook chart"))
        return out

    # ------------------------------------------------------------------
    def solve(self, inst: Instance, options: dict[str, Any],
              context: dict[str, Any] | None = None) -> MethodResult:
        res = self._new_result()
        t0 = time.time()
        ok, reason = self.available()
        if not ok:
            res.status, res.error = "SKIPPED", reason
            return res

        fd, log_file = tempfile.mkstemp(prefix="gurobi_", suffix=".log")
        os.close(fd)
        try:
            cells = self.build_cells(inst, options, log_file)
            livelog.attach_file(log_file)
            nb.close_figures()
            outcome = nb.execute_cells(cells)
            res.figures = nb.capture_figures()
            if getattr(self, "_mpl_note", ""):
                res.notes.append(self._mpl_note)
            if outcome.soft_errors:
                res.notes.append("노트북 시각화 셀에서 오류가 났습니다 (해에는 영향 없음). 로그를 확인하세요.")
            res.log = outcome.log
            try:
                with open(log_file, encoding="utf-8", errors="replace") as f:
                    slog = f.read()
                if slog.strip():
                    res.log += "\n===== Gurobi solver log =====\n" + slog
            except OSError:
                pass

            if outcome.was_cancelled:
                res.status = "CANCELLED"
                res.notes.append("사용자가 실행을 취소했습니다.")
                res.wall_time = time.time() - t0
                return res
            if not outcome.ok:
                res.status, res.error = "ERROR", outcome.error
                res.wall_time = time.time() - t0
                return res

            ns = outcome.namespace
            model = ns["model"]
            res.status = GRB_STATUS.get(int(model.Status), str(model.Status))
            res.num_vars = int(model.NumVars)
            res.num_constraints = int(model.NumConstrs)
            res.objective_name = str(ns.get("OBJ", options.get("objective", "makespan")))
            try:
                res.solve_time = float(model.Runtime)
            except Exception:
                pass
            if getattr(model, "SolCount", 0) < 1:
                res.feasible = False
                res.notes.append("실행 가능한 해를 찾지 못했습니다.")
            else:
                res.feasible = True
                res.objective = float(model.ObjVal)
                res.makespan = float(ns["makespan"].X)
                try:
                    res.best_bound = float(model.ObjBound)
                    res.mip_gap = float(model.MIPGap)
                except Exception:
                    pass
                self.extract(res, ns, inst, options)
        except Exception:
            res.status, res.error = "ERROR", traceback.format_exc()
        finally:
            try:
                os.unlink(log_file)
            except OSError:
                pass
        res.wall_time = time.time() - t0
        return res.finalize(t_hat=float(inst.params.t_hat))

    def extract(self, res: MethodResult, ns: dict[str, Any], inst: Instance,
                options: dict[str, Any]) -> None:
        raise NotImplementedError
