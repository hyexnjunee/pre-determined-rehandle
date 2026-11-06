"""
보완 ver 어댑터 — model/Reha_Crane_predetermined_swap.ipynb
===========================================================

협업 모델(coop)에 **본 작업 순서 교환**을 더한 보완판.
같은 크레인에서 predetermined 순서로 K 칸(SWAP_WINDOW) 안의 두 본 작업은 순서를 바꿀 수 있다.
기본은 어떤 유형도 고정하지 않는다 (적하끼리도 바꿀 수 있음, 2026-11-06). 선박 적재 순서를 지켜야 하면
swap_fixed_ld 로 적하끼리 고정 (SWAP_FIXED_TYPES = ("LD",)). K = 0 이면 협업 모델과 같은 식.

"교환만" (지원 없이 순서 교환만) 은 같은 방법에서 allow_assist = False.
"""

from __future__ import annotations

from typing import Any

from ..schema import Instance
from .base import MethodResult
from .coop import CoopRehandle


def swap_fixed_types(options: dict[str, Any]) -> tuple[str, ...]:
    out = []
    if options.get("swap_fixed_ld", False):
        out.append("LD")
    if options.get("swap_fixed_ds", False):
        out.append("DS")
    return tuple(out)


class CoopSwap(CoopRehandle):
    key = "coop_swap"
    label = "협업 MIP · 순서 교환 (보완 ver)"
    short = "협업+교환"
    role = "proposed"
    swap = True
    notebook = "Reha_Crane_predetermined_swap.ipynb"
    paper = "Predetermined Sequence + Cooperative Rehandle Assignment + Limited Swap"
    description = ("협업 모델에 본 작업 순서 교환을 더함: 같은 크레인에서 원래 순서로 K칸 안의 두 본 작업은 순서를 바꿀 수 있음 "
                   "(기본은 적하끼리도 바꿀 수 있음, 고정은 옵션). 반대편 크레인이 rehandle 을 하는 동안 원래 크레인이 다른 작업을 먼저 할 수 있음.")
    options = {
        **CoopRehandle.options,
        "swap_window": {"type": "number", "default": 2,
                        "label": "순서 교환 범위 K: 원래 순서로 몇 칸 안까지 (SWAP_WINDOW, 0 = 협업 모델과 같음)"},
        "swap_fixed_ld": {"type": "checkbox", "default": False,
                          "label": "적하끼리는 순서 고정 (선박 적재 순서를 꼭 지킬 때, 기본 끔)"},
        "swap_fixed_ds": {"type": "checkbox", "default": False,
                          "label": "양하끼리도 순서 고정"},
    }

    def switches(self, options: dict[str, Any]) -> dict[str, str]:
        out = super().switches(options)
        out["SWAP_WINDOW"] = repr(max(0, int(float(options.get("swap_window", 2) or 0))))
        out["SWAP_FIXED_TYPES"] = repr(swap_fixed_types(options))
        return out

    def extract(self, res: MethodResult, ns: dict[str, Any], inst: Instance,
                options: dict[str, Any]) -> None:
        super().extract(res, ns, inst, options)
        sw, pairs = ns.get("sw"), ns.get("Swap_Pairs", [])
        swapped = [(i, j) for (i, j, v) in pairs if sw is not None and sw[i, j].X < 0.5]
        K = int(ns.get("SWAP_WINDOW", 0))
        # 실제 순서 · 원래 위치에서 몇 칸 움직였나
        moved = {}
        for c, seq in (("SC", [j.id for j in inst.seq_sc]), ("LC", [j.id for j in inst.seq_lc])):
            real = sorted(seq, key=lambda t: float(ns["s"][t].X))
            for k, t in enumerate(real):
                moved[t] = k - seq.index(t)
        typ = lambda t: ns["Tasks"][t]["type"]
        by_type = {}
        for i, j in swapped:
            k = "-".join(sorted((typ(i), typ(j))))
            by_type[k] = by_type.get(k, 0) + 1
        res.metrics.update(
            swap_window=K, swap_fixed_types=list(ns.get("SWAP_FIXED_TYPES", ())),
            swap_pairs=len(pairs), n_swaps=len(swapped), swapped_pairs=[list(p) for p in swapped],
            n_swaps_ld=by_type.get("LD-LD", 0), swaps_by_type=by_type,
            max_shift=max((abs(v) for v in moved.values()), default=0),
            moved_jobs=sum(1 for v in moved.values() if v))
        res.notes.append(
            f"순서 교환 (보완 ver): 원래 순서로 {K}칸 안의 본 작업끼리 순서를 바꿀 수 있음, "
            f"고정 = {', '.join(ns.get('SWAP_FIXED_TYPES', ())) or '없음'}끼리. "
            f"바꾼 쌍 {len(swapped)}개 / 바꿀 수 있는 쌍 {len(pairs)}개, 자리를 옮긴 본 작업 {res.metrics['moved_jobs']}개 "
            f"(최대 {res.metrics['max_shift']}칸)."
            + (" 유형별: " + ", ".join(f"{k} {v}" for k, v in sorted(by_type.items())) if by_type else ""))
        if swapped:
            res.notes.append("바꾼 쌍: " + ", ".join(f"{j} → {i}" for i, j in swapped[:12])
                             + (" …" if len(swapped) > 12 else "") + "  (화살표 왼쪽을 먼저)")
