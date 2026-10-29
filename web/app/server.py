"""
웹 서버 (표준 라이브러리만 사용)
================================

외부 패키지 의존성이 없다. Gurobi / CPLEX 가 설치된 바로 그 파이썬으로
실행하면 그대로 동작한다.

    python run_server.py            # http://127.0.0.1:8766

API
---
GET    /api/config                     방법론 목록 + 기본 인스턴스
POST   /api/validate                   인스턴스 검증 + 방법론별 호환성
POST   /api/run                        실행 시작 -> {run_id}
GET    /api/jobs/<run_id>              진행 상황 / 결과 폴링
GET    /api/instances                  저장된 인스턴스 목록
GET    /api/instance_sets              실험 인스턴스 세트 (롤링 5시간 등) 목록
GET    /api/instance_sets/<세트>/<id>  세트의 인스턴스 하나
POST   /api/instance_sets/save         세트를 인스턴스 목록에 저장
GET    /api/instances/<id>
POST   /api/instances                  인스턴스 저장
DELETE /api/instances/<id>
GET    /api/runs                       저장된 결과 목록
GET    /api/runs/<id>
DELETE /api/runs/<id>
GET    /api/runs/<id>/log?method=...
GET    /api/runs.csv                   전체 결과 비교표 CSV
GET    /api/runs/<id>/schedule.csv     한 실행의 스케줄 CSV
"""

from __future__ import annotations

import hmac
import json
import os
import sys
import threading
import time
import traceback
import uuid
from typing import Any
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs, unquote

from contextlib import redirect_stderr, redirect_stdout

from . import limits
from . import livelog
from . import nbrunner
from .registry import build_registry, default_notebook_dir
from .comparison import comparison_metrics
from .methods.notebook_model import OBJECTIVES
from .schema import Instance, default_instance, small_instance, validate
from .storage import Storage

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STATIC_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")
APP_DIR = os.path.dirname(os.path.abspath(__file__))
SERVER_STARTED = time.time()


def code_versions() -> dict:
    """
    화면 / 서버 코드 버전.
      ui      : index.html 수정 시각 → 열려 있는 탭이 옛 화면인지 판단 (새로고침 필요)
      stale   : 서버가 뜬 뒤 수정된 .py 파일 → 서버 재시작 필요
    """
    try:
        ui = int(os.path.getmtime(os.path.join(STATIC_DIR, "index.html")))
    except OSError:
        ui = 0
    web_dir = os.path.dirname(APP_DIR)
    paths = [os.path.join(web_dir, "run_server.py")]
    for dirpath, dirnames, files in os.walk(APP_DIR):
        dirnames[:] = [d for d in dirnames if d not in ("__pycache__", "static")]
        paths += [os.path.join(dirpath, f) for f in files if f.endswith(".py")]
    stale = []
    for p in paths:
        try:
            if os.path.getmtime(p) > SERVER_STARTED + 1:
                stale.append(os.path.relpath(p, web_dir))
        except OSError:
            pass
    return {"ui": ui, "stale_py": sorted(stale), "started": int(SERVER_STARTED)}


class JobManager:
    """
    실행 관리자.

    공유 서버이므로 실제 솔브는 한 번에 하나만 돌린다(노트북 실행 자체가 전역
    stdout 을 가로채기도 하고, 여러 명이 동시에 수십 코어를 잡으면 서버가 죽는다).
    나머지 요청은 대기열에 들어가고, 화면에 몇 번째인지 표시된다.
    """

    def __init__(self, registry, storage: Storage):
        self.registry = registry
        self.storage = storage
        self.jobs: dict[str, dict] = {}
        self.lock = threading.Lock()
        self.run_lock = threading.Lock()

    def _queue_position(self, run_id: str) -> int:
        """대기 중인 작업 가운데 몇 번째인지 (1 = 다음 차례)."""
        waiting = sorted(
            (j for j in self.jobs.values() if j["status"] == "queued"),
            key=lambda j: j["created_at"],
        )
        for i, j in enumerate(waiting, start=1):
            if j["run_id"] == run_id:
                return i
        return 0

    def start(self, payload: dict) -> str:
        inst = Instance.from_dict(payload.get("instance") or {})
        # 레지스트리 순서로 정렬한다. Euler 처럼 앞선 방법론의 배정을 물려받는
        # 경우가 있어서, MIP 가 먼저 끝나 있어야 한다.
        order = list(self.registry.keys())
        methods = sorted(
            {m for m in (payload.get("methods") or []) if m in self.registry},
            key=order.index,
        )
        raw_options = payload.get("options") or {}
        # 목적함수는 두 모델이 반드시 같아야 비교가 공정하다 → 실행 단위로 하나만 받는다
        objective = payload.get("objective") or "truck_wait"
        if objective not in OBJECTIVES:
            objective = "truck_wait"
        options: dict[str, dict] = {}
        limit_notes: list[str] = []
        for key in methods:
            clamped, notes = limits.clamp_options(raw_options.get(key) or {})
            clamped["objective"] = objective
            options[key] = clamped
            for n in notes:
                limit_notes.append(f"[{key}] {n}")

        run_id = "run_" + time.strftime("%Y%m%d_%H%M%S") + "_" + uuid.uuid4().hex[:4]

        # 실험 이름은 사용자가 정한다. 같은 이름이 이미 있으면 (2), (3) ... 을 붙여
        # 보관함에서 구분되게 한다. 진행 중인 다른 작업의 이름도 함께 본다.
        with self.lock:
            pending = {j["label"] for j in self.jobs.values()
                       if j["status"] in ("queued", "running")}
        label = self.storage.unique_label(
            payload.get("label") or inst.name or inst.id, used=pending)

        job = {
            "run_id": run_id,
            "created_at": time.time(),
            "owner": (payload.get("owner") or "").strip()[:40],
            "limit_notes": limit_notes,
            "label": label,
            "instance_id": inst.id,
            "instance_name": inst.name,
            "num_jobs": inst.num_jobs(),
            "num_rehandles": inst.num_rehandles(),
            "objective": objective,
            "methods": methods,
            "options": options,
            "status": "queued",
            "current": None,
            "progress": 0,
            "results": [],
            "logs": {},
            "figures": {},
            "live": livelog.LiveLog(mirror=sys.__stdout__),
            "cancel_requested": False,
            "saved": False,
            "save_result": bool(payload.get("save_result", True)),
            "save_instance": bool(payload.get("save_instance", False)),
            "error": None,
        }
        with self.lock:
            self.jobs[run_id] = job

        t = threading.Thread(target=self._run, args=(job, inst), daemon=True)
        t.start()
        return run_id

    # ------------------------------------------------------------------
    def cancel(self, run_id: str) -> bool:
        """실행 중이거나 대기 중인 작업을 취소한다."""
        with self.lock:
            job = self.jobs.get(run_id)
            if job is None or job["status"] in ("done", "error", "cancelled"):
                return False
            job["cancel_requested"] = True
            running = job["status"] == "running"
        if running:
            # 솔버 콜백이 이 이벤트를 보고 즉시 terminate 한다
            nbrunner.request_cancel()
        return True

    def _run(self, job: dict, inst: Instance) -> None:
        # 앞 작업이 끝날 때까지 대기 (status = queued)
        self.run_lock.acquire()
        try:
            with self.lock:
                if job["cancel_requested"]:
                    job["status"] = "cancelled"
                    return
                job["status"] = "running"

            nbrunner.clear_cancel()
            livelog.set_current(job["live"])
            try:
                # 실행 중 출력은 전부 이 작업의 실시간 로그로 흘려보낸다.
                # (노트북 셀 print, 어댑터 출력 모두 여기로 들어온다)
                with redirect_stdout(job["live"]), redirect_stderr(job["live"]):
                    self._run_locked(job, inst)
            finally:
                livelog.set_current(None)
                nbrunner.clear_cancel()
        finally:
            self.run_lock.release()

    def _run_locked(self, job: dict, inst: Instance) -> None:
        done: dict[str, Any] = {}      # 끝난 방법론의 결과 (뒤 방법론에 넘겨준다)
        try:
            if job["save_instance"]:
                # save_instance 가 중복을 정리하면서 실제 id 를 돌려준다
                saved_id = self.storage.save_instance(inst)
                with self.lock:
                    job["instance_id"] = saved_id
                    job["instance_name"] = inst.name

            for idx, key in enumerate(job["methods"]):
                if job["cancel_requested"]:
                    print(f"\n[취소] 남은 방법론을 실행하지 않습니다 "
                          f"({len(job['methods']) - idx}개).")
                    break
                with self.lock:
                    job["current"] = key
                    job["progress"] = idx
                method = self.registry[key]
                options = dict(job["options"].get(key, {}))
                print(f"\n{'=' * 60}\n[{key}] 실행 시작\n{'=' * 60}")

                problems = method.check_compatibility(inst)
                if problems:
                    result = method._new_result()
                    result.status = "SKIPPED"
                    result.error = " / ".join(problems)
                    result.notes.append("인스턴스가 이 방법론의 가정과 맞지 않아 건너뛰었습니다.")
                else:
                    try:
                        # 앞서 끝난 방법론의 결과를 넘겨준다.
                        # (Euler 가 KAIST 가 정한 배정을 물려받는 데 쓴다)
                        result = method.solve(inst, options,
                                              context={"results": dict(done)})
                    except Exception:
                        result = method._new_result()
                        result.status = "ERROR"
                        result.error = traceback.format_exc()
                done[key] = result
                # 휴리스틱은 노트북을 실행하지 않으므로, 노트북 시각화 셀로 따로 그린다
                if (result.feasible and result.schedule and not getattr(result, "figures", None)
                        and getattr(method, "role", "") == "heuristic"
                        and options.get("draw_notebook_figure", True)):
                    try:
                        from . import nbchart
                        nbdir = os.path.dirname(getattr(self.registry.get("coop"), "notebook_path", "") or "")
                        figs, note = nbchart.draw(result, inst, nbdir)
                        result.figures = figs
                        if note:
                            result.notes.append(note)
                    except Exception:
                        result.notes.append("노트북 원본 차트 그리기 실패: " + traceback.format_exc(limit=1))
                # 휴리스틱 등: 같은 문제를 푼 MIP 결과(comparison_reference) 대비 품질 지표
                if (result.metrics or {}).get("comparison_reference"):
                    try:
                        result.metrics.update(comparison_metrics(result, done))
                    except Exception:
                        result.notes.append("MIP 대비 지표 계산 실패: " + traceback.format_exc(limit=1))

                data = result.to_dict(include_log=False, include_figures=False)
                log = getattr(result, "log", "") or ""
                if result.error and result.status == "ERROR":
                    log = (log + "\n===== adapter error =====\n" + result.error)
                with self.lock:
                    job["results"].append(data)
                    job["logs"][key] = log
                    job["figures"][key] = list(getattr(result, "figures", []) or [])
                    job["progress"] = idx + 1

            record = {
                "run_id": job["run_id"],
                "created_at": job["created_at"],
                "finished_at": time.time(),
                "owner": job["owner"],
                "limit_notes": job["limit_notes"],
                "label": job["label"],
                "instance_id": job["instance_id"],
                "instance_name": job["instance_name"],
                "num_jobs": job["num_jobs"],
                "num_rehandles": job["num_rehandles"],
                "objective": job["objective"],
                "options": job["options"],
                "instance": inst.to_dict(),
                "results": job["results"],
            }
            cancelled = job["cancel_requested"]
            record["cancelled"] = cancelled

            # 취소했더라도 그때까지 끝난 결과는 저장한다 (일부러 버리지 않는다)
            if job["save_result"] and job["results"]:
                self.storage.save_run(record, job["logs"], inst, job["figures"])
                with self.lock:
                    job["saved"] = True
            with self.lock:
                job["record"] = record
                job["status"] = "cancelled" if cancelled else "done"
                job["current"] = None
        except Exception:
            with self.lock:
                job["status"] = "error"
                job["error"] = traceback.format_exc()

    def overview(self) -> list[dict]:
        """지금 서버에서 돌고 있는/대기 중인 작업을 한 번에 (원자적으로) 본다."""
        with self.lock:
            jobs = sorted(self.jobs.values(), key=lambda j: j["created_at"])
            waiting = [j["run_id"] for j in jobs if j["status"] == "queued"]
            return [{
                "run_id": j["run_id"],
                "owner": j["owner"],
                "label": j["label"],
                "status": j["status"],
                "current": j["current"],
                "progress": j["progress"],
                "total": len(j["methods"]),
                "created_at": j["created_at"],
                "queue_position": (waiting.index(j["run_id"]) + 1
                                   if j["run_id"] in waiting else 0),
            } for j in jobs]

    def snapshot(self, run_id: str, log_from: int = 0) -> dict | None:
        with self.lock:
            job = self.jobs.get(run_id)
            if job is None:
                return None
            live = job["live"]
        # 락 밖에서 (파일 tail 이 들어갈 수 있으므로)
        chunk, log_offset = live.read_since(log_from)
        with self.lock:
            return {
                "live_chunk": chunk,
                "log_offset": log_offset,
                "cancel_requested": job["cancel_requested"],
                "run_id": job["run_id"],
                "instance_id": job["instance_id"],
                "status": job["status"],
                "queue_position": (self._queue_position(run_id)
                                   if job["status"] == "queued" else 0),
                "owner": job["owner"],
                "limit_notes": job["limit_notes"],
                "current": job["current"],
                "progress": job["progress"],
                "total": len(job["methods"]),
                "methods": job["methods"],
                "results": list(job["results"]),
                "saved": job["saved"],
                "error": job["error"],
                "label": job["label"],
                "objective": job["objective"],
                "created_at": job["created_at"],
                "logs": {k: (v[-200_000:] if v else "") for k, v in job["logs"].items()},
                "figures": {k: list(v) for k, v in job["figures"].items()},
            }


LOGIN_PAGE = """<!DOCTYPE html><html lang="ko"><head><meta charset="utf-8">
<title>Rehandle 협업 스케줄링 — 접속</title>
<style>
 body{font:14px/1.6 -apple-system,BlinkMacSystemFont,"Segoe UI","Noto Sans KR",sans-serif;
      background:#f6f7f9;color:#1c2126;display:flex;align-items:center;
      justify-content:center;height:100vh;margin:0}
 .box{background:#fff;border:1px solid #e3e6ea;border-radius:12px;padding:28px 30px;width:390px}
 h1{font-size:17px;margin:0 0 6px}
 p{color:#6b7683;font-size:13px;margin:0 0 18px}
 input{width:100%;padding:9px 11px;border:1px solid #e3e6ea;border-radius:7px;font-size:14px}
 button{width:100%;margin-top:10px;background:#2f6df6;color:#fff;border:none;padding:10px;
        border-radius:7px;font-size:14px;font-weight:600;cursor:pointer}
 .err{background:#fbe9e7;color:#8e2a1e;border:1px solid #f2c9c4;border-radius:7px;
      padding:8px 11px;font-size:12.5px;margin-bottom:14px}
</style></head><body>
<form class="box" method="get" action="/">
  <h1>Rehandle 협업 스케줄링 Web</h1>
  <p>연구실 공용 서버입니다. 접속 토큰을 입력하세요.</p>
  __ERROR__
  <input name="token" type="password" placeholder="접속 토큰" autofocus>
  <button type="submit">접속</button>
</form></body></html>"""


class Handler(BaseHTTPRequestHandler):
    server_version = "RehandleWeb/1.0"
    registry = {}
    storage: Storage = None      # type: ignore
    jobs: JobManager = None      # type: ignore
    token: str = ""              # 비어 있으면 인증 없음
    realdata = None
    candidates_dir = ""          # 실험용 실데이터 후보 구간 (analysis/instance_candidates)

    # -- 유틸 --------------------------------------------------------------
    def log_message(self, fmt, *args):    # 콘솔 소음 줄이기
        if "/api/jobs" in (self.path or ""):
            return
        super().log_message(fmt, *args)

    def _send(self, code: int, body: bytes, ctype: str) -> None:
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def _json(self, data, code: int = 200) -> None:
        self._send(code, json.dumps(data, ensure_ascii=False, default=str).encode("utf-8"),
                   "application/json; charset=utf-8")

    def _text(self, text: str, code: int = 200,
              ctype: str = "text/plain; charset=utf-8") -> None:
        self._send(code, text.encode("utf-8"), ctype)

    # -- 인증 --------------------------------------------------------------
    def _token_ok(self, candidate: str) -> bool:
        return bool(candidate) and hmac.compare_digest(candidate, Handler.token)

    def _authorized(self, query: dict) -> bool:
        """토큰이 설정된 경우에만 검사. 쿠키 / 헤더 / ?token= 셋 다 허용."""
        if not Handler.token:
            return True
        if self._token_ok(self.headers.get("X-Auth-Token") or ""):
            return True
        if self._token_ok((query.get("token") or [""])[0]):
            return True
        for part in (self.headers.get("Cookie") or "").split(";"):
            name, _, value = part.strip().partition("=")
            if name == "cb_token" and self._token_ok(value):
                return True
        return False

    def _send_login(self, failed: bool) -> None:
        err = ('<div class="err">토큰이 올바르지 않습니다.</div>' if failed else "")
        page = LOGIN_PAGE.replace("__ERROR__", err)
        self._send(401, page.encode("utf-8"), "text/html; charset=utf-8")

    def _maybe_set_cookie(self, query: dict) -> bool:
        """?token= 으로 들어오면 쿠키를 심고 / 로 보낸다."""
        supplied = (query.get("token") or [""])[0]
        if not supplied or not self._token_ok(supplied):
            return False
        self.send_response(302)
        self.send_header("Location", "/")
        self.send_header("Set-Cookie",
                         f"cb_token={Handler.token}; Path=/; HttpOnly; SameSite=Lax; Max-Age=2592000")
        self.send_header("Content-Length", "0")
        self.end_headers()
        return True

    def _body(self) -> dict:
        length = int(self.headers.get("Content-Length") or 0)
        if not length:
            return {}
        raw = self.rfile.read(length)
        try:
            return json.loads(raw.decode("utf-8"))
        except Exception:
            return {}

    # -- 라우팅 ------------------------------------------------------------
    def do_GET(self):
        parsed = urlparse(self.path)
        path = unquote(parsed.path)
        query = parse_qs(parsed.query)
        try:
            if Handler.token and path in ("/", "/index.html") and "token" in query:
                if self._maybe_set_cookie(query):
                    return
                return self._send_login(failed=True)
            if not self._authorized(query):
                if path.startswith("/api/"):
                    return self._json({"error": "unauthorized"}, 401)
                return self._send_login(failed=False)

            if path in ("/", "/index.html"):
                return self._static("index.html")
            if path.startswith("/static/"):
                return self._static(path[len("/static/"):])

            if path == "/api/config":
                return self._json({
                    "methods": [m.info() for m in self.registry.values()],
                    "default_instance": default_instance().to_dict(),
                    "small_instance": small_instance().to_dict(),
                    "data_dir": self.storage.data_dir,
                    "notebook_dir": getattr(Handler, "notebook_dir", ""),
                    "realdata_dir": Handler.realdata.source_dir if Handler.realdata else "",
                    "limits": limits.summary(),
                    "shared": bool(Handler.token),
                    "hostname": os.uname().nodename if hasattr(os, "uname") else "",
                    "version": code_versions(),
                })
            if path == "/api/version":
                return self._json(code_versions())
            if path == "/api/instances":
                return self._json({"instances": self.storage.list_instances()})
            if path == "/api/realdata/status":
                return self._json(Handler.realdata.status())
            if path == "/api/realdata/candidates":
                return self._json(load_candidates(Handler.candidates_dir))
            if path == "/api/realdata/day":
                day = (query.get("date") or [""])[0]
                mode = (query.get("ld_rh") or ["auto"])[0]
                return self._json(Handler.realdata.day_summary(day, mode))
            if path == "/api/instance_sets":
                from . import instance_sets as isets
                out = []
                for name in isets.SETS:
                    d = isets.load_set(name, Handler.storage.data_dir)
                    out.append({k: v for k, v in d.items() if k != "instances"})
                return self._json({"sets": out})
            if path.startswith("/api/instance_sets/"):
                from . import instance_sets as isets
                parts = path[len("/api/instance_sets/"):].split("/")
                if len(parts) == 2:
                    d = isets.load_set(parts[0], Handler.storage.data_dir)
                    inst = d["instances"].get(parts[1])
                    if inst is None:
                        return self._json({"error": "not found"}, 404)
                    return self._json({"instance": inst.to_dict()})
            if path == "/api/generator/preset":
                from . import generator
                kind = (query.get("kind") or [""])[0]
                return self._json(generator.QUEUE_PRESET if kind == "queue"
                                  else generator.LD_PRESET if kind == "ld"
                                  else generator.ROLLING_PRESET if kind == "rolling" else generator.REAL_PRESET)
            if path.startswith("/api/instances/") and path.endswith("/runs"):
                iid = path[len("/api/instances/"):-len("/runs")]
                return self._json({"runs": self.storage.runs_for_instance(iid)})
            if path.startswith("/api/instances/"):
                inst = self.storage.load_instance(path.rsplit("/", 1)[-1])
                if inst is None:
                    return self._json({"error": "not found"}, 404)
                return self._json({"instance": inst.to_dict()})
            if path == "/api/runs":
                return self._json({"runs": self.storage.list_runs()})
            if path == "/api/table":
                ids = [x for x in (query.get("ids") or [""])[0].split(",") if x]
                return self._json(self.storage.table_rows(ids or None))
            if path == "/api/runs.csv":
                return self._text(self.storage.runs_csv(), ctype="text/csv; charset=utf-8")
            if path == "/api/jobs":
                return self._json({"jobs": self.jobs.overview()})
            if path.startswith("/api/jobs/"):
                try:
                    log_from = int((query.get("log_from") or ["0"])[0])
                except ValueError:
                    log_from = 0
                snap = self.jobs.snapshot(path.rsplit("/", 1)[-1], log_from)
                if snap is None:
                    return self._json({"error": "not found"}, 404)
                return self._json(snap)
            if path.startswith("/api/runs/"):
                rest = path[len("/api/runs/"):]
                if rest.endswith("/schedule.csv"):
                    rid = rest[: -len("/schedule.csv")]
                    return self._text(self.storage.run_schedule_csv(rid),
                                      ctype="text/csv; charset=utf-8")
                if rest.endswith("/log"):
                    rid = rest[: -len("/log")]
                    method = (query.get("method") or [""])[0]
                    log = self.storage.load_log(rid, method)
                    if log is None:
                        return self._text("(저장된 로그가 없습니다)", 404)
                    return self._text(log)
                if rest.endswith("/figure"):
                    rid = rest[: -len("/figure")]
                    method = (query.get("method") or [""])[0]
                    index = int((query.get("i") or ["0"])[0])
                    png = self.storage.load_figure(rid, method, index)
                    if png is None:
                        return self._text("not found", 404)
                    return self._send(200, png, "image/png")
                if rest.endswith("/figures"):
                    rid = rest[: -len("/figures")]
                    rec = self.storage.load_run(rid) or {}
                    counts = {r.get("method"): self.storage.count_figures(rid, r.get("method"))
                              for r in rec.get("results", [])}
                    return self._json({"figures": counts})
                rec = self.storage.load_run(rest)
                if rec is None:
                    return self._json({"error": "not found"}, 404)
                return self._json({"run": rec})

            return self._json({"error": "unknown endpoint"}, 404)
        except Exception:
            return self._json({"error": traceback.format_exc()}, 500)

    def do_POST(self):
        parsed = urlparse(self.path)
        path = unquote(parsed.path)
        try:
            if not self._authorized(parse_qs(parsed.query)):
                return self._json({"error": "unauthorized"}, 401)
            body = self._body()
            if path == "/api/validate":
                inst = Instance.from_dict(body.get("instance") or {})
                report = validate(inst)
                compat = {}
                for key, method in self.registry.items():
                    ok, reason = method.available()
                    compat[key] = {
                        "available": ok,
                        "unavailable_reason": reason,
                        "problems": method.check_compatibility(inst) if not report["errors"] else [],
                    }
                return self._json({"validation": report, "compatibility": compat})

            if path == "/api/run":
                run_id = self.jobs.start(body)
                return self._json({"run_id": run_id})

            if path.startswith("/api/instances/") and path.endswith("/rename"):
                iid = path[len("/api/instances/"):-len("/rename")]
                final = self.storage.rename_instance(iid, body.get("name", ""))
                if final is None:
                    return self._json({"error": "이름을 바꾸지 못했습니다"}, 400)
                return self._json({"name": final})

            if path.startswith("/api/runs/") and path.endswith("/rename"):
                rid = path[len("/api/runs/"):-len("/rename")]
                final = self.storage.rename_run(rid, body.get("label", ""))
                if final is None:
                    return self._json({"error": "이름을 바꾸지 못했습니다"}, 400)
                return self._json({"label": final})

            if path.startswith("/api/jobs/") and path.endswith("/cancel"):
                rid = path[len("/api/jobs/"):-len("/cancel")]
                return self._json({"cancelled": self.jobs.cancel(rid)})

            if path == "/api/generate":
                from . import generator
                from .schema import Params
                known = {k: v for k, v in (body.get("params") or {}).items()
                         if k in Params.__dataclass_fields__}
                params = Params(**known)
                seed = body.get("seed")
                g = lambda k, d, f=int: f(body.get(k, d) if body.get(k, d) not in (None, "") else d)
                insts = generator.generate_batch(
                    count=g("count", 1),
                    seed=None if seed in (None, "") else int(seed),
                    n_bay=g("n_bay", 30), n_ds=g("n_ds", 3), n_ld=g("n_ld", 2),
                    n_gi=g("n_gi", 2), n_go=g("n_go", 5),
                    rh_prob=g("rh_prob", 0.6, float), rh_max=g("rh_max", 3),
                    rh_prob_go=(None if body.get("rh_prob_go") in (None, "") else float(body["rh_prob_go"])),
                    rh_prob_ld=(None if body.get("rh_prob_ld") in (None, "") else float(body["rh_prob_ld"])),
                    rh_dist=("data" if body.get("rh_dist") == "data" else "uniform"),
                    rh_move_prob=g("rh_move_prob", 0.3, float),
                    rh_move_span=g("rh_move_span", 6),
                    sc_gap=g("sc_gap", 10.0, float), lc_gap=g("lc_gap", 10.0, float),
                    arrival=(body.get("arrival") if body.get("arrival") in ("even", "poisson", "burst") else "even"),
                    burst_size=g("burst_size", 3),
                    go_bay=(body.get("go_bay") if body.get("go_bay") in ("uniform", "sea", "land") else "uniform"),
                    params=params, name=(body.get("name") or None),
                )
                saved = []
                if body.get("save"):
                    for it in insts:
                        saved.append(self.storage.save_instance(it))
                return self._json({
                    "instances": [i.to_dict() for i in insts],
                    "saved_ids": saved,
                })

            if path == "/api/realdata/index":
                rd = Handler.realdata
                t0 = time.time()
                idx = rd._load_index(build=True)
                if not idx:
                    return self._json({"error": f"원본 CSV 를 찾지 못했습니다: {rd.source_dir}"}, 400)
                st = rd.status()
                st["build_sec"] = round(time.time() - t0, 1)
                return self._json(st)

            if path == "/api/realdata/build":
                rd = Handler.realdata
                inst, meta = rd.build(
                    body.get("date"), body.get("block"), body.get("start") or "09:00",
                    float(body.get("minutes") or 60), params=body.get("params") or {},
                    safety_margin=body.get("safety_margin"), name=body.get("name") or None,
                    use_real_init=bool(body.get("use_real_init", True)),
                    ld_rh=str(body.get("ld_rh") or "auto"))
                report = validate(inst)
                saved = None
                if body.get("save") and not report["errors"]:
                    saved = self.storage.save_instance(inst)
                return self._json({"instance": inst.to_dict(), "meta": meta,
                                   "validation": report, "saved_id": saved})

            if path == "/api/instance_sets/save":
                # 실험 세트를 인스턴스 목록에 저장 (group 을 주면 그 묶음만)
                from . import instance_sets as isets
                d = isets.load_set(str(body.get("set") or "rolling5h"), Handler.storage.data_dir)
                grp = body.get("group") or ""
                saved = [self.storage.save_instance(d["instances"][r["id"]])
                         for r in d["rows"] if not grp or r["group"] == grp]
                return self._json({"saved_ids": saved})

            if path == "/api/realdata/candidates/save":
                # 추천 구간을 한꺼번에 인스턴스 목록에 저장 (이름 앞에 묶음 표시)
                cand = load_candidates(Handler.candidates_dir)
                want = set(body.get("groups") or [])
                rd, saved, failed = Handler.realdata, [], []
                for c in cand.get("rows", []):
                    if want and c["group"] not in want:
                        continue
                    try:
                        inst, meta = rd.build(c["day"], c["block"], c["start"], float(body.get("minutes") or 60),
                                              params=body.get("params") or {},
                                              safety_margin=body.get("safety_margin"),
                                              name=f"[추천 {c['tag']}] {c['block']} {c['day']} {c['start']} +{int(float(body.get('minutes') or 60))}분",
                                              use_real_init=bool(body.get("use_real_init", True)),
                                              ld_rh=str(body.get("ld_rh") or "auto"))
                        if validate(inst)["errors"]:
                            raise ValueError("검증 실패")
                        saved.append(self.storage.save_instance(inst))
                    except Exception as e:
                        failed.append(f"{c['block']} {c['day']} {c['start']}: {e}")
                return self._json({"saved_ids": saved, "failed": failed})

            if path == "/api/instances":
                inst = Instance.from_dict(body.get("instance") or {})
                report = validate(inst)
                if report["errors"]:
                    return self._json({"error": "검증 실패", "validation": report}, 400)
                iid = self.storage.save_instance(inst)
                return self._json({"instance_id": iid})

            return self._json({"error": "unknown endpoint"}, 404)
        except Exception:
            return self._json({"error": traceback.format_exc()}, 500)

    def do_DELETE(self):
        parsed = urlparse(self.path)
        path = unquote(parsed.path)
        try:
            if not self._authorized(parse_qs(parsed.query)):
                return self._json({"error": "unauthorized"}, 401)
            if path.startswith("/api/instances/"):
                ok = self.storage.delete_instance(path.rsplit("/", 1)[-1])
                return self._json({"deleted": ok})
            if path.startswith("/api/runs/"):
                ok = self.storage.delete_run(path.rsplit("/", 1)[-1])
                return self._json({"deleted": ok})
            return self._json({"error": "unknown endpoint"}, 404)
        except Exception:
            return self._json({"error": traceback.format_exc()}, 500)

    # -- 정적 파일 ---------------------------------------------------------
    def _static(self, rel: str) -> None:
        rel = rel.lstrip("/")
        path = os.path.normpath(os.path.join(STATIC_DIR, rel))
        if not path.startswith(STATIC_DIR) or not os.path.isfile(path):
            return self._text("not found", 404)
        ctype = {
            ".html": "text/html; charset=utf-8",
            ".js": "application/javascript; charset=utf-8",
            ".css": "text/css; charset=utf-8",
            ".json": "application/json; charset=utf-8",
        }.get(os.path.splitext(path)[1], "application/octet-stream")
        with open(path, "rb") as f:
            self._send(200, f.read(), ctype)


def load_candidates(folder: str) -> dict:
    """analysis/instance_candidates/candidates.csv (+ candidates_eval.csv) 를 읽는다."""
    import csv
    path = os.path.join(folder, "candidates.csv")
    if not os.path.exists(path):
        return {"available": False, "dir": folder, "rows": []}
    with open(path, encoding="utf-8-sig") as f:
        rows = list(csv.DictReader(f))
    ev = {}
    epath = os.path.join(folder, "candidates_eval.csv")
    if os.path.exists(epath):
        with open(epath, encoding="utf-8-sig") as f:
            for r in csv.DictReader(f):
                ev[(r["day"], r["block"], r["start"])] = r

    def num(v):
        try:
            return float(v)
        except (TypeError, ValueError):
            return None
    out = []
    for r in rows:
        e = ev.get((r["day"], r["block"], r["start"]), {})
        nh, w = num(e.get("window_nohelp")), num(e.get("window_rule"))
        bs, cp = num(e.get("baseline")), num(e.get("coop"))
        out.append({
            "group": r["group"], "tag": r["group"].split()[0],
            "day": r["day"], "block": r["block"], "start": r["start"],
            "n_go": int(float(r["n_go"])), "n_gi": int(float(r["n_gi"])), "n_sc_main": int(float(r["n_sc_main"])),
            "go_rh": int(float(r["go_rh"])), "go_wait_med_min": num(r["go_wait_med_min"]),
            "go_queued_at_start": int(float(r["go_queued_at_start"])),
            "sc_busy": num(r["sc_busy"]), "lc_busy": num(r["lc_busy"]),
            "window_gain_pct": (round(100 * (nh - w) / nh, 1) if nh and w is not None else None),
            "window_assist": num(e.get("window_rule_assist")),
            "coop_gain_pct": (round(100 * (bs - cp) / bs, 1)
                              if bs and cp is not None and e.get("coop_status") == "OPTIMAL" else None),
        })
    groups = list(dict.fromkeys(r["group"] for r in out))
    return {"available": True, "dir": folder, "groups": groups, "rows": out}


def create_server(host: str, port: int, base_dir: str = BASE_DIR,
                  notebook_dir: str | None = None,
                  data_dir: str | None = None,
                  token: str = "",
                  realdata_dir: str | None = None) -> ThreadingHTTPServer:
    notebook_dir = notebook_dir or default_notebook_dir(base_dir)
    data_dir = data_dir or os.path.join(base_dir, "data")
    registry = build_registry(notebook_dir)
    storage = Storage(data_dir)

    Handler.registry = registry
    Handler.storage = storage
    Handler.jobs = JobManager(registry, storage)
    Handler.token = token or ""
    Handler.notebook_dir = notebook_dir
    from .realdata import RealData
    Handler.realdata = RealData(realdata_dir or os.path.join(base_dir, "..", "data"),
                                os.path.join(data_dir, "realcache"))
    Handler.candidates_dir = os.path.normpath(os.path.join(base_dir, "..", "analysis", "instance_candidates"))

    httpd = ThreadingHTTPServer((host, port), Handler)
    return httpd
