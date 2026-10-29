"""
통합 인스턴스 스키마 — Predetermined Sequence + Rehandle
=========================================================

두 노트북(비교군 / 협업 모델)이 공유하는 단 하나의 문제 표현.
각 어댑터가 이 스키마를 노트북의 `Seq_SC`, `Seq_LC`, `N_BAY`, 파라미터 셀로 번역한다.

JSON 예시
---------
{
  "id": "toy_12",
  "name": "Toy 12 jobs",
  "block":  {"n_bay": 30},
  "params": {"t_hat": 1, "pick": 1, "drop": 1, "safety_margin": 1,
             "rh_inplace": 3, "default_ready": 0},
  "seq_sc": [{"id": "DS_1", "type": "DS", "bay": 20},
             {"id": "LD_1", "type": "LD", "bay": 8, "r_c": 1}],
  "seq_lc": [{"id": "GO_1", "type": "GO", "bay": 14, "r_c": 3, "a": 0},
             {"id": "GO_2", "type": "GO", "bay": 16, "r_c": 3, "rh_d": [16, 16, 19], "a": 10}]
}

좌표 규약
---------
- 해측 TP(Sea TP) = bay 0, 야드 = bay 1 ~ n_bay, 육측 TP(Land TP) = bay n_bay+1
- SC(해측, 홀수호기)는 bay 0 에서, LC(육측, 짝수호기)는 bay n_bay+1 에서 출발
- DS : 해측 TP → bay      LD : bay → 해측 TP      (SC 가 수행)
- GI : 육측 TP → bay      GO : bay → 육측 TP      (LC 가 수행)
- seq_sc / seq_lc 의 **순서가 곧 predetermined sequence** 이다.
- r_c   : 그 작업 전에 치워야 할 컨테이너 수 (LD, GO 만)
- rh_d  : rehandle 별 내려놓을 bay (비우면 같은 bay 에서 옮기는 제자리 rehandle)
- a     : 작업 가능 시각 (GO 는 트럭 도착 시각). 비우면 params.default_ready
"""

from __future__ import annotations

import json
import re
import time
import uuid
from dataclasses import dataclass, field, asdict
from typing import Any

SC_TYPES = ("DS", "LD")
LC_TYPES = ("GI", "GO")
RH_TYPES = ("LD", "GO")          # rehandle 이 붙을 수 있는 작업
CRANES = ("SC", "LC")

_ID_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


@dataclass
class Block:
    n_bay: int = 30
    #: 크레인 시작 위치 (bay). 비우면 SC = 해측 TP 0, LC = 육측 TP n_bay+1
    sc_init: float | None = None
    lc_init: float | None = None
    #: 크레인이 비는 시각 (롤링 호라이즌에서 하던 작업이 끝나는 시각). 0 이면 처음부터 비어 있음
    sc_ready: float = 0.0
    lc_ready: float = 0.0


@dataclass
class Params:
    t_hat: float = 1.0           # bay 1칸 이동시간
    pick: float = 1.0
    drop: float = 1.0
    safety_margin: int = 1       # 안전거리 = safety_margin + 1 bay
    rh_inplace: float = 3.0      # 같은 bay 안에서 옮기는 rehandle 1개 처리시간
    default_ready: float = 0.0   # a 를 비웠을 때의 기본값


@dataclass
class Job:
    id: str
    type: str                    # DS | LD | GI | GO
    bay: int
    r_c: int = 0
    rh_d: list[int] | None = None
    a: float | None = None


@dataclass
class Instance:
    id: str = ""
    name: str = ""
    block: Block = field(default_factory=Block)
    params: Params = field(default_factory=Params)
    seq_sc: list[Job] = field(default_factory=list)
    seq_lc: list[Job] = field(default_factory=list)
    created_at: float = 0.0
    note: str = ""
    #: 부가 정보 (실데이터 출처, 같은 구간의 실제 기록 등). 모델에는 들어가지 않는다.
    meta: dict = field(default_factory=dict)

    # -- 직렬화 -------------------------------------------------------------
    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def to_json(self, indent: int = 2) -> str:
        return json.dumps(self.to_dict(), indent=indent, ensure_ascii=False)

    @staticmethod
    def from_dict(data: dict[str, Any]) -> "Instance":
        def jobs(rows):
            out = []
            for r in rows or []:
                r = dict(r)
                rh = r.get("rh_d")
                if isinstance(rh, str):
                    rh = [int(x) for x in re.split(r"[,\s]+", rh.strip()) if x]
                r["rh_d"] = [int(x) for x in rh] if rh else None
                r["r_c"] = int(r.get("r_c") or 0)
                r["bay"] = int(r.get("bay") or 0)
                a = r.get("a")
                r["a"] = None if a in (None, "") else float(a)
                out.append(Job(**{k: r[k] for k in ("id", "type", "bay", "r_c", "rh_d", "a") if k in r}))
            return out

        known_p = {k: v for k, v in (data.get("params") or {}).items()
                   if k in Params.__dataclass_fields__}
        return Instance(
            id=data.get("id") or new_instance_id(),
            name=data.get("name") or "untitled",
            block=Block(**{k: v for k, v in (data.get("block") or {}).items()
                           if k in Block.__dataclass_fields__}),
            params=Params(**known_p),
            seq_sc=jobs(data.get("seq_sc")),
            seq_lc=jobs(data.get("seq_lc")),
            created_at=data.get("created_at") or time.time(),
            note=data.get("note", ""),
            meta=dict(data.get("meta") or {}),
        )

    # -- 조회 ---------------------------------------------------------------
    def jobs(self) -> list[Job]:
        return list(self.seq_sc) + list(self.seq_lc)

    def num_jobs(self) -> int:
        return len(self.seq_sc) + len(self.seq_lc)

    def num_rehandles(self) -> int:
        return sum(int(j.r_c or 0) for j in self.jobs())

    def ready(self, j: Job) -> float:
        return self.params.default_ready if j.a is None else j.a

    def counts(self) -> dict[str, int]:
        c = {t: 0 for t in SC_TYPES + LC_TYPES}
        for j in self.jobs():
            c[j.type] = c.get(j.type, 0) + 1
        return c


def new_instance_id() -> str:
    return "inst_" + uuid.uuid4().hex[:10]


# ----------------------------------------------------------------------------
# 검증
# ----------------------------------------------------------------------------
def validate(inst: Instance) -> dict[str, list[str]]:
    errors: list[str] = []
    warnings: list[str] = []
    n = int(inst.block.n_bay)
    if n < 3:
        errors.append(f"야드 bay 수가 너무 적습니다: {n}")

    p = inst.params
    if p.t_hat <= 0:
        errors.append("t̂ 는 0 보다 커야 합니다.")
    if p.safety_margin < 0:
        errors.append("안전 여유는 0 이상이어야 합니다.")
    if p.rh_inplace <= 0:
        errors.append("제자리 rehandle 처리시간은 0 보다 커야 합니다.")

    b = inst.block
    for nm, v in (("SC 시작 위치", b.sc_init), ("LC 시작 위치", b.lc_init)):
        if v is not None and not (0 <= float(v) <= n + 1):
            errors.append(f"{nm} {v} 가 0 ~ {n + 1} 밖입니다.")
    sc0 = 0 if b.sc_init is None else float(b.sc_init)
    lc0 = n + 1 if b.lc_init is None else float(b.lc_init)
    if sc0 + p.safety_margin + 1 > lc0 + 1e-9:
        errors.append(f"시작 위치가 안전거리보다 가깝습니다 (SC {sc0}, LC {lc0}, 안전거리 {p.safety_margin + 1}).")

    if not inst.jobs():
        errors.append("작업이 1개도 없습니다.")
    if not inst.seq_sc:
        warnings.append("SC(해측) 작업이 없습니다. 반대편 지원 여지를 보려면 DS/LD 가 있는 편이 좋습니다.")
    if not inst.seq_lc:
        warnings.append("LC(육측) 작업이 없습니다.")

    seen: set[str] = set()
    rh_ids: set[str] = set()
    for crane, seq, allowed in (("SC", inst.seq_sc, SC_TYPES), ("LC", inst.seq_lc, LC_TYPES)):
        for k, j in enumerate(seq, start=1):
            tag = f"{crane} #{k} ({j.id})"
            if not _ID_RE.match(j.id or ""):
                errors.append(f"{tag}: id 는 영문/숫자/_ 만 쓸 수 있습니다.")
            if j.id in seen:
                errors.append(f"{tag}: id 중복")
            seen.add(j.id)
            if j.type not in allowed:
                errors.append(f"{tag}: {crane} 는 {'/'.join(allowed)} 작업만 수행합니다 (지금 {j.type}).")
            if not (1 <= int(j.bay) <= n):
                errors.append(f"{tag}: bay={j.bay} 가 야드 범위 1~{n} 밖입니다.")
            if j.r_c < 0:
                errors.append(f"{tag}: r_c 는 0 이상이어야 합니다.")
            if j.r_c and j.type not in RH_TYPES:
                errors.append(f"{tag}: rehandle 은 LD / GO 에만 붙일 수 있습니다.")
            if j.rh_d:
                if len(j.rh_d) != j.r_c:
                    errors.append(f"{tag}: rh_d 개수({len(j.rh_d)})가 r_c({j.r_c})와 다릅니다. "
                                  f"비우면 모두 제자리 rehandle 입니다.")
                for v in j.rh_d:
                    if not (1 <= int(v) <= n):
                        errors.append(f"{tag}: rh_d 의 bay {v} 가 야드 범위 1~{n} 밖입니다.")
            if inst.ready(j) < 0:
                errors.append(f"{tag}: a 는 0 이상이어야 합니다.")
            for r in range(1, int(j.r_c or 0) + 1):
                rh_ids.add(f"{j.id}_r{r}")

    clash = rh_ids & seen
    if clash:
        errors.append("rehandle 작업 이름(작업id_rN)과 겹치는 작업 id 가 있습니다: "
                      + ", ".join(sorted(clash)))
    if not errors and inst.num_rehandles() == 0:
        warnings.append("rehandle 이 하나도 없습니다. 두 모델 결과가 같게 나옵니다.")
    if inst.num_jobs() + inst.num_rehandles() > 60:
        warnings.append("작업 + rehandle 이 60개를 넘습니다. 협업 모델은 순서 변수가 "
                        "rehandle 수 × 작업 수로 늘어나 오래 걸릴 수 있습니다.")
    return {"errors": errors, "warnings": warnings}


# ----------------------------------------------------------------------------
# 기본 인스턴스
# ----------------------------------------------------------------------------
def default_instance() -> Instance:
    """노트북(Reha_Crane_predetermined / baseline)에 들어 있던 toy 인스턴스."""
    sc = [
        Job("DS_1", "DS", 20),
        Job("LD_1", "LD", 8, r_c=1),
        Job("DS_2", "DS", 10),
        Job("LD_2", "LD", 15, r_c=2, rh_d=[15, 13]),
        Job("DS_3", "DS", 6),
    ]
    lc = [
        Job("GO_1", "GO", 14, r_c=3, a=0),
        Job("GI_1", "GI", 22),
        Job("GO_2", "GO", 16, r_c=3, rh_d=[16, 16, 19], a=10),
        Job("GO_3", "GO", 12, r_c=2, a=20),
        Job("GI_2", "GI", 26),
        Job("GO_4", "GO", 18, r_c=2, a=30),
        Job("GO_5", "GO", 25, r_c=0, a=40),
    ]
    return Instance(id="toy_12", name="Toy 12작업 (노트북 기본)", block=Block(30),
                    params=Params(), seq_sc=sc, seq_lc=lc, created_at=time.time(),
                    note="Reha_Crane_predetermined.ipynb / Reha_Crane_baseline.ipynb 에 들어 있던 인스턴스")


def small_instance() -> Instance:
    """빠른 연결 확인용 5 작업."""
    sc = [Job("DS_1", "DS", 9), Job("LD_1", "LD", 6, r_c=1)]
    lc = [Job("GO_1", "GO", 10, r_c=2, a=0), Job("GI_1", "GI", 16),
          Job("GO_2", "GO", 13, r_c=1, a=15)]
    return Instance(id="small_5", name="소형 5작업 (연결 확인용)", block=Block(20),
                    params=Params(), seq_sc=sc, seq_lc=lc, created_at=time.time(),
                    note="솔버 연결 확인용")
