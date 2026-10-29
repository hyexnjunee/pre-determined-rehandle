"""
저장소 (Instances / Runs)
=========================

data/
  instances/<instance_id>.json      저장한 인스턴스
  runs/<run_id>/run.json            실행 요약 + 모든 방법론 결과 (로그 제외)
  runs/<run_id>/log_<method>.txt    방법론별 솔버 로그
  runs/<run_id>/instance.json       그 실행에 쓰인 인스턴스 스냅샷

인스턴스 저장 / 결과 저장은 각각 켜고 끌 수 있다 (실행 요청의 save_instance,
save_result 플래그). 저장하지 않으면 파일은 만들어지지 않는다.
"""

from __future__ import annotations

import base64
import hashlib
import csv
import io
import json
import os
import re
import shutil
import time
import uuid
from typing import Any

from .schema import Instance

_SAFE = re.compile(r"[^A-Za-z0-9_.-]")


def _safe(name: str) -> str:
    return _SAFE.sub("_", name)[:80] or "unnamed"


class Storage:
    def __init__(self, data_dir: str):
        self.data_dir = data_dir
        self.instances_dir = os.path.join(data_dir, "instances")
        self.runs_dir = os.path.join(data_dir, "runs")
        os.makedirs(self.instances_dir, exist_ok=True)
        os.makedirs(self.runs_dir, exist_ok=True)

    # -- 인스턴스 ----------------------------------------------------------
    @staticmethod
    def content_hash(inst: Instance) -> str:
        """
        '같은 문제인가'를 판별하는 지문.

        id·이름·메모·저장시각은 뺀다. 이름만 바꿔 저장해도 같은 인스턴스로 보고,
        작업이나 파라미터가 하나라도 다르면 다른 인스턴스로 본다.
        """
        data = inst.to_dict()
        for k in ("id", "name", "note", "created_at"):
            data.pop(k, None)
        blob = json.dumps(data, sort_keys=True, ensure_ascii=False)
        return hashlib.sha1(blob.encode("utf-8")).hexdigest()[:12]

    def _existing(self) -> list[dict[str, Any]]:
        out = []
        for fn in sorted(os.listdir(self.instances_dir)):
            if not fn.endswith(".json"):
                continue
            try:
                with open(os.path.join(self.instances_dir, fn), encoding="utf-8") as f:
                    out.append(json.load(f))
            except Exception:
                continue
        return out

    def save_instance(self, inst: Instance) -> str:
        """
        인스턴스를 저장하고 실제로 쓰인 id 를 돌려준다.

        예전에는 같은 id 로 저장하면 그냥 덮어썼다. 그래서 기본 인스턴스를
        여러 번 돌리면 목록에 하나만 남고, 조금 고쳐서 다시 돌리면 이전 것이
        사라졌다. 지금은 내용 지문으로 판단한다.

          · 내용이 같다  -> 이미 있는 것을 그대로 재사용 (중복 파일 안 만듦)
          · 내용이 다른데 id 가 겹친다 -> 새 id 와 "이름 (2)" 로 따로 보존
        """
        if not inst.created_at:
            inst.created_at = time.time()

        digest = self.content_hash(inst)
        existing = self._existing()

        # 1) 내용이 완전히 같은 것이 이미 있으면 그것을 쓴다
        for data in existing:
            if data.get("content_hash") == digest:
                return data.get("id")

        # 2) id / 이름 충돌 피하기
        base_id = _safe(inst.id or ("inst_" + uuid.uuid4().hex[:10]))
        taken_ids = {d.get("id") for d in existing}
        new_id, n = base_id, 1
        while new_id in taken_ids:
            n += 1
            new_id = f"{base_id}__{n}"

        base_name = inst.name or base_id
        taken_names = {d.get("name") for d in existing}
        new_name, n = base_name, 1
        while new_name in taken_names:
            n += 1
            new_name = f"{base_name} ({n})"

        inst.id, inst.name = new_id, new_name
        payload = inst.to_dict()
        payload["content_hash"] = digest

        path = os.path.join(self.instances_dir, _safe(new_id) + ".json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2, ensure_ascii=False)
        return new_id

    def unique_label(self, label: str, used: set[str] | None = None) -> str:
        """실행 이름이 겹치면 '이름 (2)', '이름 (3)' 으로 붙여 준다."""
        label = (label or "").strip() or "실험"
        taken = {r.get("label") for r in self.list_runs()}
        if used:
            taken |= set(used)
        if label not in taken:
            return label
        n = 1
        while True:
            n += 1
            candidate = f"{label} ({n})"
            if candidate not in taken:
                return candidate

    def rename_instance(self, instance_id: str, new_name: str) -> str | None:
        """
        저장된 인스턴스의 이름만 바꾼다 (내용·id 는 그대로).

        나중에 목록에서 알아보기 쉽게 하려는 용도라, 이름이 겹치면 (2) 를 붙인다.
        """
        path = os.path.join(self.instances_dir, _safe(instance_id) + ".json")
        if not os.path.exists(path):
            return None
        with open(path, encoding="utf-8") as f:
            data = json.load(f)

        wanted = (new_name or "").strip()
        if not wanted:
            return None

        taken = {d.get("name") for d in self._existing() if d.get("id") != instance_id}
        final, n = wanted, 1
        while final in taken:
            n += 1
            final = f"{wanted} ({n})"

        data["name"] = final
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        return final

    def rename_run(self, run_id: str, new_label: str) -> str | None:
        """저장된 실행의 이름(라벨)을 바꾼다."""
        path = os.path.join(self.runs_dir, _safe(run_id), "run.json")
        if not os.path.exists(path):
            return None
        with open(path, encoding="utf-8") as f:
            rec = json.load(f)

        wanted = (new_label or "").strip()
        if not wanted:
            return None

        taken = {r.get("label") for r in self.list_runs() if r.get("run_id") != run_id}
        final, n = wanted, 1
        while final in taken:
            n += 1
            final = f"{wanted} ({n})"

        rec["label"] = final
        with open(path, "w", encoding="utf-8") as f:
            json.dump(rec, f, indent=2, ensure_ascii=False)
        return final

    def runs_for_instance(self, instance_id: str) -> list[dict[str, Any]]:
        """이 인스턴스로 돌렸던 실행 기록."""
        return [r for r in self.list_runs() if r.get("instance_id") == instance_id]

    def list_instances(self) -> list[dict[str, Any]]:
        out = []
        for fn in sorted(os.listdir(self.instances_dir)):
            if not fn.endswith(".json"):
                continue
            try:
                with open(os.path.join(self.instances_dir, fn), encoding="utf-8") as f:
                    data = json.load(f)
            except Exception:
                continue
            sc, lc = data.get("seq_sc", []), data.get("seq_lc", [])
            out.append({
                "id": data.get("id", fn[:-5]),
                "name": data.get("name", ""),
                "num_jobs": len(sc) + len(lc),
                "num_sc": len(sc),
                "num_lc": len(lc),
                "num_rehandles": sum(int(j.get("r_c") or 0) for j in sc + lc),
                "n_bay": (data.get("block") or {}).get("n_bay", 0),
                "created_at": data.get("created_at", 0),
                "note": data.get("note", ""),
                "content_hash": data.get("content_hash", ""),
            })
        out.sort(key=lambda d: d.get("created_at", 0), reverse=True)

        # 각 인스턴스로 몇 번 돌렸는지 붙여 준다 (목록에서 바로 보이게)
        counts: dict[str, int] = {}
        for r in self.list_runs():
            iid = r.get("instance_id")
            if iid:
                counts[iid] = counts.get(iid, 0) + 1
        for d in out:
            d["run_count"] = counts.get(d["id"], 0)
        return out

    def load_instance(self, instance_id: str) -> Instance | None:
        path = os.path.join(self.instances_dir, _safe(instance_id) + ".json")
        if not os.path.exists(path):
            return None
        with open(path, encoding="utf-8") as f:
            return Instance.from_dict(json.load(f))

    def delete_instance(self, instance_id: str) -> bool:
        path = os.path.join(self.instances_dir, _safe(instance_id) + ".json")
        if not os.path.exists(path):
            return False
        try:
            os.unlink(path)
        except OSError:
            return False
        return True

    # -- 실행 결과 ---------------------------------------------------------
    def save_run(self, record: dict[str, Any], logs: dict[str, str],
                 instance: Instance,
                 figures: dict[str, list[str]] | None = None) -> str:
        """
        figures: {method_key: [base64 png, ...]}
        노트북이 그린 그림은 JSON 을 부풀리지 않도록 별도 PNG 파일로 저장한다.
        """
        run_id = record["run_id"]
        d = os.path.join(self.runs_dir, _safe(run_id))
        os.makedirs(d, exist_ok=True)

        for method, imgs in (figures or {}).items():
            for n, b64 in enumerate(imgs):
                try:
                    raw = base64.b64decode(b64)
                except Exception:
                    continue
                with open(os.path.join(d, f"fig_{_safe(method)}_{n}.png"), "wb") as f:
                    f.write(raw)

        with open(os.path.join(d, "run.json"), "w", encoding="utf-8") as f:
            json.dump(record, f, indent=2, ensure_ascii=False)
        with open(os.path.join(d, "instance.json"), "w", encoding="utf-8") as f:
            f.write(instance.to_json())
        for method, log in logs.items():
            if not log:
                continue
            with open(os.path.join(d, f"log_{_safe(method)}.txt"), "w",
                      encoding="utf-8") as f:
                f.write(log)
        return run_id

    def list_runs(self) -> list[dict[str, Any]]:
        out = []
        if not os.path.isdir(self.runs_dir):
            return out
        for name in os.listdir(self.runs_dir):
            path = os.path.join(self.runs_dir, name, "run.json")
            if not os.path.exists(path):
                continue
            try:
                with open(path, encoding="utf-8") as f:
                    rec = json.load(f)
            except Exception:
                continue
            out.append({
                "run_id": rec.get("run_id", name),
                "label": rec.get("label", ""),
                "owner": rec.get("owner", ""),
                "created_at": rec.get("created_at", 0),
                "instance_name": rec.get("instance_name", ""),
                "instance_id": rec.get("instance_id", ""),
                "num_jobs": rec.get("num_jobs", 0),
                "num_rehandles": rec.get("num_rehandles", 0),
                "objective": rec.get("objective", ""),
                "methods": [r.get("method") for r in rec.get("results", [])],
                "summary": [
                    {
                        "method": r.get("method"),
                        "label": r.get("label"),
                        "status": r.get("status"),
                        "makespan": r.get("makespan"),
                        "go_wait": (r.get("metrics") or {}).get("go_wait_sum"),
                        "truck_wait": (r.get("metrics") or {}).get("truck_wait_sum"),
                        "n_assisted": (r.get("metrics") or {}).get("n_assisted"),
                        "solve_time": r.get("solve_time"),
                        "mip_gap": r.get("mip_gap"),
                    }
                    for r in rec.get("results", [])
                ],
            })
        out.sort(key=lambda d: d.get("created_at", 0), reverse=True)
        return out

    def load_run(self, run_id: str) -> dict[str, Any] | None:
        path = os.path.join(self.runs_dir, _safe(run_id), "run.json")
        if not os.path.exists(path):
            return None
        with open(path, encoding="utf-8") as f:
            return json.load(f)

    def load_log(self, run_id: str, method: str) -> str | None:
        path = os.path.join(self.runs_dir, _safe(run_id), f"log_{_safe(method)}.txt")
        if not os.path.exists(path):
            return None
        with open(path, encoding="utf-8", errors="replace") as f:
            return f.read()

    def load_figure(self, run_id: str, method: str, index: int) -> bytes | None:
        path = os.path.join(self.runs_dir, _safe(run_id),
                            f"fig_{_safe(method)}_{int(index)}.png")
        if not os.path.exists(path):
            return None
        with open(path, "rb") as f:
            return f.read()

    def count_figures(self, run_id: str, method: str) -> int:
        d = os.path.join(self.runs_dir, _safe(run_id))
        if not os.path.isdir(d):
            return 0
        prefix = f"fig_{_safe(method)}_"
        return len([fn for fn in os.listdir(d)
                    if fn.startswith(prefix) and fn.endswith(".png")])

    def delete_run(self, run_id: str) -> bool:
        d = os.path.join(self.runs_dir, _safe(run_id))
        if not os.path.isdir(d):
            return False
        try:
            shutil.rmtree(d)
        except OSError:
            # 일부 파일시스템(마운트된 폴더 등)에서 rmtree 가 막히는 경우
            for fn in os.listdir(d):
                try:
                    os.unlink(os.path.join(d, fn))
                except OSError:
                    pass
            try:
                os.rmdir(d)
            except OSError:
                return False
        return True

    # -- 논문용 집계 --------------------------------------------------------
    def table_rows(self, run_ids: list[str] | None = None) -> dict[str, Any]:
        """
        저장된 실행들을 '인스턴스 × 방법론' 표로 집계한다.

        논문 표는 보통 한 행이 인스턴스이고 열이 방법론이다. 여기서는 한 행을
        '한 번의 실행'으로 두되 인스턴스 정보를 같이 실어, 같은 인스턴스를 여러 번
        돌린 경우도 구분해서 볼 수 있게 한다.
        """
        rows: list[dict[str, Any]] = []
        method_keys: list[str] = []
        method_labels: dict[str, str] = {}

        for meta in self.list_runs():
            rid = meta["run_id"]
            if run_ids and rid not in run_ids:
                continue
            rec = self.load_run(rid)
            if not rec:
                continue

            inst = rec.get("instance") or {}
            jobs = (inst.get("seq_sc") or []) + (inst.get("seq_lc") or [])
            per: dict[str, Any] = {}

            for r in rec.get("results", []):
                key = r.get("method")
                if not key:
                    continue
                if key not in method_labels:
                    method_keys.append(key)
                    method_labels[key] = r.get("label") or key

                m = r.get("metrics") or {}
                status = str(r.get("status") or "")
                per[key] = {
                    "makespan": r.get("makespan"),
                    "go_wait": m.get("go_wait_sum"),
                    "truck_wait": m.get("truck_wait_sum"),
                    "tw_int": m.get("truck_wait_internal"),
                    "tw_ext": m.get("truck_wait_external"),
                    "go_avg": m.get("go_wait_avg"),
                    "assist": m.get("n_assisted"),
                    "n_rh": m.get("n_rehandles"),
                    "objective": r.get("objective"),
                    "objective_name": r.get("objective_name"),
                    "bound": r.get("best_bound"),
                    "gap": r.get("mip_gap"),
                    "time": r.get("solve_time"),
                    "wall": r.get("wall_time"),
                    "status": status,
                    "vars": r.get("num_vars"),
                    "constrs": r.get("num_constraints"),
                    "optimal": status.upper().startswith("OPTIMAL"),
                    "rpd": m.get("rpd_pct"),
                    "dev": m.get("incumbent_deviation_pct"),
                    "speedup": m.get("wall_speedup"),
                }

            rows.append({
                "run_id": rid,
                "label": rec.get("label") or rid,
                "owner": rec.get("owner", ""),
                "created_at": rec.get("created_at", 0),
                "instance_id": rec.get("instance_id", ""),
                "instance_name": rec.get("instance_name") or inst.get("name", ""),
                "n_jobs": len(jobs),
                "n_rh": sum(int(j.get("r_c") or 0) for j in jobs),
                "n_bay": (inst.get("block") or {}).get("n_bay", 0),
                "objective": rec.get("objective", ""),
                "methods": per,
            })

        rows.sort(key=lambda r: (r["n_jobs"], r["created_at"]))
        return {
            "rows": rows,
            "methods": [{"key": k, "label": method_labels[k]} for k in method_keys],
        }

    # -- 내보내기 ----------------------------------------------------------
    def runs_csv(self) -> str:
        buf = io.StringIO()
        w = csv.writer(buf)
        w.writerow(["run_id", "created_at", "owner", "label", "instance_id",
                    "instance_name",
                    "num_jobs", "num_rehandles", "objective_name", "method", "status",
                    "makespan", "truck_wait_sum", "truck_wait_internal",
                    "truck_wait_external", "go_wait_sum", "n_assisted", "objective",
                    "best_bound", "mip_gap", "solve_time", "wall_time", "num_vars",
                    "num_constraints", "reference_method", "reference_status", "rpd_pct",
                    "incumbent_deviation_pct", "bound_gap_pct", "wall_speedup"])
        for meta in self.list_runs():
            rec = self.load_run(meta["run_id"]) or {}
            stamp = time.strftime("%Y-%m-%d %H:%M:%S",
                                  time.localtime(rec.get("created_at", 0)))
            for r in rec.get("results", []):
                w.writerow([
                    rec.get("run_id"), stamp, rec.get("owner", ""), rec.get("label", ""),
                    rec.get("instance_id", ""), rec.get("instance_name", ""),
                    rec.get("num_jobs", ""), rec.get("num_rehandles", ""),
                    r.get("objective_name"), r.get("method"), r.get("status"),
                    r.get("makespan"), (r.get("metrics") or {}).get("truck_wait_sum"),
                    (r.get("metrics") or {}).get("truck_wait_internal"),
                    (r.get("metrics") or {}).get("truck_wait_external"),
                    (r.get("metrics") or {}).get("go_wait_sum"),
                    (r.get("metrics") or {}).get("n_assisted"), r.get("objective"),
                    r.get("best_bound"), r.get("mip_gap"),
                    r.get("solve_time"), r.get("wall_time"),
                    r.get("num_vars"), r.get("num_constraints"),
                    *((r.get("metrics") or {}).get(k) for k in ("reference_method", "reference_status",
                      "rpd_pct", "incumbent_deviation_pct", "bound_gap_pct", "wall_speedup")),
                ])
        return buf.getvalue()

    def run_schedule_csv(self, run_id: str) -> str:
        rec = self.load_run(run_id) or {}
        buf = io.StringIO()
        w = csv.writer(buf)
        w.writerow(["method", "crane", "seq", "task", "kind", "job_type", "parent",
                    "home", "assisted", "o", "d", "ready", "start", "end", "duration",
                    "rh_time", "truck_out", "truck_wait"])
        for r in rec.get("results", []):
            for e in r.get("schedule", []):
                w.writerow([r.get("method"), e.get("crane"), e.get("seq"),
                            e.get("task"), e.get("kind"), e.get("job_type"),
                            e.get("parent"), e.get("home"), e.get("assisted"),
                            e.get("o"), e.get("d"), e.get("ready"),
                            e.get("start"), e.get("end"), e.get("duration"),
                            e.get("rh_time"),
                            e.get("truck_out") if e.get("kind") == "main" else "",
                            (round(e.get("truck_out", 0) - e.get("ready", 0), 4)
                             if e.get("kind") == "main" else "")])
        return buf.getvalue()
