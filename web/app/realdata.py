"""
실데이터 인스턴스 가져오기
==========================

동원 ARMG 작업 기록(CSV)에서 블록 · 날짜 · 시간 구간을 골라 통합 인스턴스로 만든다.

원본 위치
---------
기본은 web/ 옆의 ../data 폴더. 그 안에서 JOB_TYPE · MACH_NO · WRK_START_DT 열이 있는
CSV 중 가장 큰 파일(보통 WAS_ARMG_CAPA_202501_202503.csv, 3개월치)을 쓴다.
없으면 후보 블록 추출 파일(*_paired_crane_full_day.csv)을 모두 합쳐 쓴다.

처음 한 번 원본을 읽어 날짜별 색인(web/data/realcache/)을 만든다. 원본 크기·수정시각이
바뀌면 다시 만든다.

변환 규칙
---------
- 크레인 쌍: 홀수호기 = SC(해측), 짝수호기 = LC(육측), (n, n+1) 이 한 블록
- 작업 순서: 구간 [시작, 끝) 안에서 시작한 본 작업(DS · LD · GI · GO)을 크레인별 시작시각 순으로
  → predetermined sequence. 크레인-작업 유형이 맞지 않는 작업(예: SC 가 한 GO)은 빼고 개수를 알린다.
- bay: DS · GI 는 TO_BAY, LD · GO 는 FM_BAY
- GO rehandle: RH_CNT 개수. 같은 크레인의 직전 RH_CNT 개 작업이 같은 bay 출발 AH 이면 그 AH 의
  TO_BAY 를 rh_d 로 쓴다 (복원 실패 · 다른 블록으로 옮긴 것은 같은 bay 안에서 옮긴 것으로 둔다)
- LD rehandle (ld_rh 옵션)
    "auto"   : RH_CNT 가 기록된 LD(2025-02-08 12:04 부터)는 기록을 그대로 쓰고 (GO 와 같은 방식),
               RH_CNT 가 빈 LD 는 직전 AH 로 추정한다.
               추정 = 같은 크레인이 LD 직전에 연달아 한, 같은 블록·bay·row 에서 출발한 AH
               (작업 사이 간격 ≤ 10분). 기록과 비교하면 개수 일치 99.3 %, 발생 정밀도 96.7 %,
               재현율 87.0 % (analysis/ld_rehandle/ 참고. row 조건을 빼면 정밀도 68.6 %)
    "record" : RH_CNT 기록만 (기록이 빈 LD 는 0)
    "none"   : LD rehandle 0 (예전 동작)
  기록 구간 LD 39,445건 중 3.48 % 에 rehandle (있으면 평균 1.49개), 98.6 % 를 SC 가 수행.
  LD 대상 AH 의 대부분(PF 등)은 수 시간 전 미리 정리한 것이라 rehandle 로 세지 않는다.
- 트럭 도착 a: REQ_DT − 구간 시작 (초). 구간 시작 전에 요청된 작업은 0 (이미 기다리는 중)
- 크레인 시작 위치: 구간 시작 직전에 그 크레인이 마지막으로 끝낸 작업의 도착 위치
  (DS · GI · AH → TO_BAY, LD → 해측 TP 0, GO → 육측 TP N+1)
- 시간 파라미터(초): t̂ 1.5, pick 75, drop 75, ρ 150 (원본 CSV 회귀·중앙값), 안전 여유는 가정값
- 참고값: 같은 구간의 실제 기록으로 makespan · 트럭 대기를 같은 정의로 계산해 meta.actual 에 둔다
"""

from __future__ import annotations

import calendar
import csv
import glob
import json
import os
import re
import threading
import time
from collections import Counter, defaultdict
from datetime import datetime, timedelta
from typing import Any

from .schema import Block, Instance, Job, Params, new_instance_id

#: 원본 CSV 로 추정한 시간 파라미터 (초)
REAL_PARAMS = {"t_hat": 1.5, "pick": 75, "drop": 75, "rh_inplace": 150, "default_ready": 0}
#: 안전 여유 기본값 (가정). 안전거리 = 값 + 1 bay 번호. 현장 값으로 바꿀 것.
DEFAULT_SAFETY_MARGIN = 3
#: LD 의 RH_CNT 가 기록되기 시작한 날 (원본 CSV 기준; 그 전에는 LD RH_CNT 가 모두 비어 있음)
LD_RH_RECORDED_FROM = "2025-02-08"     # 이 날 12:04 부터. 실제 판단은 RH_CNT 가 비었는지로 한다
#: 직전 AH 로 rehandle 을 추정할 때 연속 작업 사이 최대 간격 (초)
RH_INFER_GAP = 600
LD_RH_MODES = ("auto", "record", "none")

MAIN = ("DS", "LD", "GI", "GO")
NEED = ("JOB_TYPE", "MACH_NO", "WRK_START_DT")
# 색인 행: [type, mach, start, comp, req, tlock, fm_blk, fm_bay, to_blk, to_bay, rh_cnt(빈 값=None), fm_row]
COLS = ("JOB_TYPE", "MACH_NO", "WRK_START_DT", "COMP_DT", "REQ_DT", "TWIST_LOCK_DT",
        "FM_BLK", "FM_BAY", "TO_BLK", "TO_BAY", "RH_CNT", "FM_ROW")
#: 색인 형식 버전 (바뀌면 색인을 다시 만든다)
INDEX_VERSION = 2
_YARD = re.compile(r"^A\d\d$")
_LOCK = threading.Lock()


def _ts(s: str) -> float | None:
    """'31-12-2024 23:55:11' 또는 '2025-01-05 23:57:19' → 초 (타임존 없이 그대로)."""
    s = (s or "").strip()
    if not s:
        return None
    for fmt in ("%d-%m-%Y %H:%M:%S", "%Y-%m-%d %H:%M:%S"):
        try:
            return float(calendar.timegm(datetime.strptime(s[:19], fmt).timetuple()))
        except ValueError:
            continue
    return None


def _day(ts: float) -> str:
    return datetime.utcfromtimestamp(ts).strftime("%Y-%m-%d")


def _bay(s) -> int | None:
    try:
        return int(float(str(s).strip()))
    except (TypeError, ValueError):
        return None


def _mach(s) -> int | None:
    try:
        return int(float(s))
    except (TypeError, ValueError):
        return None


def rh_of(rs: list, k: int, n_bay: int, ld_mode: str = "auto") -> tuple[int, list | None, str, list[int]]:
    """
    rs[k] (LD 또는 GO) 의 rehandle → (개수, rh_d 또는 None, 출처, 쓴 AH 행 번호들).
    출처: "record" (RH_CNT) | "infer" (직전 AH 로 추정) | "" (없음)
    """
    r = rs[k]
    typ, blk, bay = r[0], r[6], r[7]
    row = r[11] if len(r) > 11 else None

    def dests(prev):
        # 같은 블록 안으로 옮긴 것만 목적 bay 로, 아니면 같은 bay 안에서 옮긴 것으로
        d = [p[9] if (p[9] is not None and 1 <= p[9] <= n_bay and p[8] == blk) else bay for p in prev]
        return None if all(v == bay for v in d) else d

    use_record = typ == "GO" or (ld_mode in ("auto", "record") and r[10] is not None)
    if typ == "LD" and ld_mode == "none":
        return 0, None, "", []
    if use_record:
        rc = int(r[10] or 0)
        if not rc:
            return 0, None, "", []
        prev = rs[max(0, k - rc):k]
        if len(prev) == rc and all(p[0] == "AH" and p[6] == blk and p[7] == bay for p in prev):
            return rc, dests(prev), "record", list(range(k - rc, k))
        return rc, None, "record", []          # 개수는 기록, 목적 bay 는 복원 실패 → 제자리
    if typ == "LD" and ld_mode == "auto":
        used, nxt = [], r[2]
        j = k - 1
        while j >= 0:
            p = rs[j]
            end = p[3] if p[3] is not None else p[2]
            prow = p[11] if len(p) > 11 else None
            if (p[0] != "AH" or p[6] != blk or p[7] != bay or nxt - end > RH_INFER_GAP
                    or (row is not None and prow is not None and prow != row)):
                break
            used.append(j)
            nxt = p[2]
            j -= 1
        if used:
            used.reverse()
            return len(used), dests([rs[i] for i in used]), "infer", used
    return 0, None, "", []


class RealData:
    def __init__(self, source_dir: str, cache_dir: str):
        self.source_dir = os.path.abspath(source_dir)
        self.cache_dir = cache_dir
        self._index: dict | None = None
        self._day_cache: dict[str, list] = {}

    # ------------------------------------------------------------------ 원본
    def sources(self) -> list[str]:
        out = []
        for p in sorted(glob.glob(os.path.join(self.source_dir, "*.csv"))):
            try:
                with open(p, encoding="utf-8-sig", newline="") as f:
                    head = next(csv.reader(f), [])
            except Exception:
                continue
            if all(c in head for c in NEED):
                out.append(p)
        if not out:
            return []
        big = max(out, key=os.path.getsize)
        # 원본(큰 파일)이 있으면 그것 하나만, 없으면 추출 파일들을 합쳐 쓴다
        if os.path.getsize(big) > 20_000_000 or len(out) == 1:
            return [big]
        return [p for p in out if "paired_crane" in os.path.basename(p)] or out

    def _signature(self, files: list[str]) -> list:
        return [[os.path.basename(p), os.path.getsize(p), int(os.path.getmtime(p))] for p in files] + [["v", INDEX_VERSION]]

    def status(self) -> dict[str, Any]:
        files = self.sources()
        idx = self._load_index(build=False)
        return {
            "available": bool(files),
            "source_dir": self.source_dir,
            "files": [os.path.basename(p) for p in files],
            "indexed": bool(idx and idx.get("signature") == self._signature(files)),
            "days": sorted((idx or {}).get("days", {}).keys()) if idx else [],
            "real_params": REAL_PARAMS,
            "default_safety_margin": DEFAULT_SAFETY_MARGIN,
            "ld_rh_recorded_from": LD_RH_RECORDED_FROM,
        }

    # ------------------------------------------------------------------ 색인
    def _load_index(self, build: bool = True) -> dict | None:
        files = self.sources()
        if not files:
            return None
        sig = self._signature(files)
        if self._index and self._index.get("signature") == sig:
            return self._index
        path = os.path.join(self.cache_dir, "index.json")
        if os.path.exists(path):
            try:
                with open(path, encoding="utf-8") as f:
                    idx = json.load(f)
                if idx.get("signature") == sig:
                    self._index = idx
                    return idx
            except Exception:
                pass
        if not build:
            return None
        with _LOCK:
            return self._build_index(files, sig)

    def _build_index(self, files: list[str], sig: list) -> dict:
        t0 = time.time()
        os.makedirs(self.cache_dir, exist_ok=True)
        by_day: dict[str, list] = defaultdict(list)
        blk_count: dict[int, Counter] = defaultdict(Counter)
        max_bay: dict[str, int] = defaultdict(int)
        seen = set()
        for p in files:
            with open(p, encoding="utf-8-sig", newline="") as f:
                for row in csv.DictReader(f):
                    st = _ts(row.get("WRK_START_DT"))
                    m = _mach(row.get("MACH_NO"))
                    if st is None or m is None:
                        continue
                    key = (row.get("JOBID") or "", m, st, row.get("JOB_TYPE"))
                    if key in seen:          # 추출 파일끼리 겹치는 행
                        continue
                    seen.add(key)
                    fb, tb = _bay(row.get("FM_BAY")), _bay(row.get("TO_BAY"))
                    fblk, tblk = (row.get("FM_BLK") or "").strip(), (row.get("TO_BLK") or "").strip()
                    for blk, b in ((fblk, fb), (tblk, tb)):
                        if _YARD.match(blk) and b is not None:
                            blk_count[m][blk] += 1
                            max_bay[blk] = max(max_bay[blk], b)
                    rc = _bay(row.get("RH_CNT"))          # 빈 값이면 None (기록 안 됨)
                    by_day[_day(st)].append([
                        (row.get("JOB_TYPE") or "").strip(), m, st, _ts(row.get("COMP_DT")),
                        _ts(row.get("REQ_DT")), _ts(row.get("TWIST_LOCK_DT")),
                        fblk, fb, tblk, tb, rc, _bay(row.get("FM_ROW")),
                    ])
        for d, rows in by_day.items():
            rows.sort(key=lambda r: (r[1], r[2]))
            with open(os.path.join(self.cache_dir, f"{d}.json"), "w", encoding="utf-8") as f:
                json.dump(rows, f, separators=(",", ":"))
        # 크레인 쌍 → 블록
        pairs = {}
        for m in sorted(blk_count):
            if m % 2 == 1 and (m + 1) in blk_count:
                c = blk_count[m] + blk_count[m + 1]
                blk = c.most_common(1)[0][0]
                pairs[blk] = [m, m + 1]
        idx = {
            "signature": sig, "built_at": time.time(), "build_sec": round(time.time() - t0, 1),
            "days": {d: len(r) for d, r in by_day.items()},
            "pairs": pairs, "max_bay": dict(max_bay),
        }
        with open(os.path.join(self.cache_dir, "index.json"), "w", encoding="utf-8") as f:
            json.dump(idx, f, ensure_ascii=False)
        self._index = idx
        self._day_cache.clear()
        return idx

    def _rows(self, day: str) -> list:
        if day in self._day_cache:
            return self._day_cache[day]
        path = os.path.join(self.cache_dir, f"{day}.json")
        rows = []
        if os.path.exists(path):
            with open(path, encoding="utf-8") as f:
                rows = json.load(f)
        if len(self._day_cache) > 12:
            self._day_cache.clear()
        self._day_cache[day] = rows
        return rows

    def _rows_around(self, day: str, cranes: tuple[int, int]) -> dict[int, list]:
        d0 = datetime.strptime(day, "%Y-%m-%d")
        out = {c: [] for c in cranes}
        for k in (-1, 0, 1):
            dd = (d0 + timedelta(days=k)).strftime("%Y-%m-%d")
            for r in self._rows(dd):
                if r[1] in out:
                    out[r[1]].append(r)
        for c in out:
            out[c].sort(key=lambda r: r[2])
        return out

    # ------------------------------------------------------------------ 조회
    def day_summary(self, day: str, ld_mode: str = "auto") -> dict[str, Any]:
        idx = self._load_index()
        if not idx:
            return {"error": "원본 CSV 를 찾지 못했습니다.", "blocks": []}
        d0 = calendar.timegm(datetime.strptime(day, "%Y-%m-%d").timetuple())
        rows = self._rows(day)
        blocks = []
        for blk, (sc, lc) in sorted(idx["pairs"].items()):
            hours = [{"main": 0, "go_rh": 0, "ld_rh": 0, "rh": 0, "sc": 0, "lc": 0} for _ in range(24)]
            n = 0
            nb = int(idx["max_bay"].get(blk, 85))
            for mach in (sc, lc):
                rs = [r for r in rows if r[1] == mach]
                for k, r in enumerate(rs):
                    if r[0] not in MAIN:
                        continue
                    h = int((r[2] - d0) // 3600)
                    if not 0 <= h < 24:
                        continue
                    n += 1
                    hours[h]["main"] += 1
                    hours[h]["sc" if mach == sc else "lc"] += 1
                    if r[0] in ("GO", "LD"):
                        rc = rh_of(rs, k, nb, ld_mode)[0]
                        hours[h]["go_rh" if r[0] == "GO" else "ld_rh"] += rc
                        hours[h]["rh"] += rc
            if n:
                blocks.append({"block": blk, "sc": sc, "lc": lc, "main": n,
                               "go_rh": sum(h["go_rh"] for h in hours),
                               "ld_rh": sum(h["ld_rh"] for h in hours), "hours": hours,
                               "n_bay": nb})
        blocks.sort(key=lambda b: -b["main"])
        lds = [r for r in rows if r[0] == "LD"]
        rec = sum(1 for r in lds if r[10] is not None)
        if ld_mode == "none" or not lds:
            src = "none"
        elif rec == len(lds):
            src = "record"
        elif rec == 0:
            src = "infer" if ld_mode == "auto" else "none"
        else:
            src = "mixed" if ld_mode == "auto" else "record"
        return {"day": day, "blocks": blocks, "ld_rh_mode": ld_mode, "ld_rh_source": src}

    # ------------------------------------------------------------------ 인스턴스
    def build(self, day: str, block: str, start: str, minutes: float,
              params: dict | None = None, safety_margin: float | None = None,
              name: str | None = None, use_real_init: bool = True,
              ld_rh: str = "auto") -> tuple[Instance, dict]:
        idx = self._load_index()
        if not idx:
            raise ValueError("원본 CSV 를 찾지 못했습니다.")
        if block not in idx["pairs"]:
            raise ValueError(f"블록 {block} 의 크레인 쌍을 찾지 못했습니다.")
        sc, lc = idx["pairs"][block]
        n_bay = int(idx["max_bay"].get(block, 85))
        t0 = calendar.timegm(datetime.strptime(f"{day} {start}", "%Y-%m-%d %H:%M").timetuple())
        t1 = t0 + float(minutes) * 60
        ld_rh = ld_rh if ld_rh in LD_RH_MODES else "auto"
        rows = self._rows_around(day, (sc, lc))
        report = {"skipped_type": Counter(), "other_moves": Counter(), "rh_restored": 0,
                  "rh_inplace_fallback": 0, "waiting_before": 0, "go_rh": 0,
                  "ld_rh": 0, "ld_rh_recorded": 0, "ld_rh_inferred": 0}

        def dest(r) -> float | None:
            t, fb, tb, tblk = r[0], r[7], r[9], r[8]
            if t == "LD":
                return 0
            if t == "GO":
                return n_bay + 1
            if tb is not None and (_YARD.match(tblk or "") or t in ("DS", "GI")):
                return tb
            return None

        seqs: dict[str, list[Job]] = {"SC": [], "LC": []}
        actual: list[dict] = []
        counters = Counter()
        init = {}
        for mach, side, allowed in ((sc, "SC", ("DS", "LD")), (lc, "LC", ("GI", "GO"))):
            rs = rows[mach]
            # 시작 위치
            pos = 0 if side == "SC" else n_bay + 1
            for r in rs:
                if r[2] >= t0:
                    break
                d = dest(r)
                if d is not None:
                    pos = d
            init[side] = pos
            consumed = set()
            for k, r in enumerate(rs):
                if not (t0 <= r[2] < t1):
                    continue
                typ = r[0]
                if typ not in MAIN:
                    continue
                if typ not in allowed:
                    report["skipped_type"][f"{side}-{typ}"] += 1
                    continue
                bay = r[9] if typ in ("DS", "GI") else r[7]
                if bay is None or not (1 <= bay <= n_bay):
                    report["skipped_type"][f"{side}-{typ}(bay?)"] += 1
                    continue
                rc, rh_d = 0, None
                if typ in ("GO", "LD"):
                    rc, rh_d, src, used = rh_of(rs, k, n_bay, ld_rh)
                    if rc:
                        report["go_rh" if typ == "GO" else "ld_rh"] += rc
                        if typ == "LD":
                            report["ld_rh_recorded" if src == "record" else "ld_rh_inferred"] += rc
                        if used:
                            report["rh_restored"] += 1
                            consumed.update(used)
                        else:
                            report["rh_inplace_fallback"] += 1
                counters[typ] += 1
                a = 0.0 if r[4] is None else max(0.0, r[4] - t0)
                if r[4] is not None and r[4] < t0:
                    report["waiting_before"] += 1
                job = Job(f"{typ}_{counters[typ]}", typ, int(bay), r_c=rc, rh_d=rh_d, a=round(a, 1))
                seqs[side].append(job)
                # 실제 기록 (같은 정의로 트럭 대기 계산)
                st = r[2] - t0
                comp = (r[3] - t0) if r[3] else None
                if comp is not None:
                    if typ in ("DS", "GI"):
                        out = (r[5] - t0) if r[5] else st + REAL_PARAMS["pick"]
                    else:
                        out = comp
                    actual.append({"type": typ, "end": comp, "wait": out - a})
            for k, r in enumerate(rs):
                if t0 <= r[2] < t1 and r[0] not in MAIN and k not in consumed:
                    report["other_moves"][f"{side}-{r[0]}"] += 1

        p = dict(REAL_PARAMS)
        p.update({k: v for k, v in (params or {}).items() if k in Params.__dataclass_fields__})
        p["safety_margin"] = DEFAULT_SAFETY_MARGIN if safety_margin in (None, "") else safety_margin
        delta = float(p["safety_margin"]) + 1
        sc_init, lc_init = (init["SC"], init["LC"]) if use_real_init else (0, n_bay + 1)
        if sc_init + delta > lc_init:
            # 기록상 위치가 안전거리보다 가까우면 서로 벌려 놓는다
            mid = (sc_init + lc_init) / 2
            sc_init, lc_init = max(0, mid - delta / 2), min(n_bay + 1, mid + delta / 2)
            if sc_init + delta > lc_init:
                sc_init, lc_init = max(0, lc_init - delta), max(lc_init, delta)
            report["init_adjusted"] = True

        def agg(types):
            v = [x["wait"] for x in actual if x["type"] in types]
            return round(sum(v), 1) if v else None
        meta = {
            "source": "realdata", "day": day, "block": block, "cranes": {"SC": sc, "LC": lc},
            "window": {"start": f"{day} {start}", "minutes": float(minutes)},
            "ld_rh_mode": ld_rh,
            "actual": {
                "makespan": round(max(x["end"] for x in actual), 1) if actual else None,
                "truck_wait_sum": agg(MAIN), "truck_wait_internal": agg(("DS", "LD")),
                "truck_wait_external": agg(("GI", "GO")), "go_wait_sum": agg(("GO",)),
                "note": "같은 구간의 실제 기록. 제외된 작업(AH·RH·MI·MO 등)과 구간 밖 작업도 실제로는 크레인 시간을 썼으므로 참고용.",
            },
            "report": {k: (dict(v) if isinstance(v, Counter) else v) for k, v in report.items()},
        }
        nm = name or f"{block} {day} {start} +{int(minutes)}분 ({sc}/{lc})"
        note = (f"실데이터: {block} SC {sc} / LC {lc}, {day} {start} 부터 {int(minutes)}분. "
                f"본 작업 {sum(len(s) for s in seqs.values())}개, GO rehandle {report['go_rh']}개, "
                f"LD rehandle {report['ld_rh']}개 (기록 {report['ld_rh_recorded']} · 직전 AH 추정 {report['ld_rh_inferred']}, "
                f"모드 {ld_rh}), 목적 bay 복원 {report['rh_restored']}건. 안전 여유 {p['safety_margin']} 는 가정값.")
        inst = Instance(
            id=new_instance_id(), name=nm,
            block=Block(n_bay=n_bay, sc_init=sc_init, lc_init=lc_init),
            params=Params(**p), seq_sc=seqs["SC"], seq_lc=seqs["LC"],
            created_at=time.time(), note=note, meta=meta,
        )
        return inst, meta
