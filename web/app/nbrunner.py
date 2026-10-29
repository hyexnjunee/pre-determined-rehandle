"""
노트북 실행 엔진 (Notebook execution engine)
============================================

설계 의도
---------
논문 모델의 "원본"은 계속 .ipynb 로 둔다. 이 시스템은 노트북을 다시 쓰지 않고,

    1) 데이터가 하드코딩된 셀만 통합 인스턴스로 갈아끼우고
    2) 시각화 셀은 건너뛰고
    3) 남은 셀을 순서대로 exec 하여
    4) 네임스페이스에서 결과를 표준 형식으로 뽑아낸다.

이렇게 하면 노트북에서 모델을 계속 수정해도 웹 시스템이 자동으로 따라간다.
(= "매번 예제 문제를 넣었다 뺐다" 하는 작업이 사라진다)
"""

from __future__ import annotations

import io
import json
import re
import sys
import threading
import time
import traceback
from contextlib import redirect_stdout, redirect_stderr
from dataclasses import dataclass, field
from typing import Any, Callable


# ----------------------------------------------------------------------------
# 노트북 로딩
# ----------------------------------------------------------------------------
def load_code_cells(nb_path: str) -> list[str]:
    with open(nb_path, "r", encoding="utf-8") as f:
        nb = json.load(f)
    cells = []
    for cell in nb.get("cells", []):
        if cell.get("cell_type") != "code":
            continue
        src = "".join(cell.get("source", []))
        if src.strip():
            cells.append(src)
    return cells


# ----------------------------------------------------------------------------
# 소스 패치 헬퍼
# ----------------------------------------------------------------------------
def find_cell(cells: list[str], marker: str) -> int:
    """marker 문자열을 (주석이 아닌 줄에서) 포함하는 첫 셀의 index."""
    for i, src in enumerate(cells):
        for line in src.splitlines():
            if line.lstrip().startswith("#"):
                continue
            if marker in line:
                return i
    raise KeyError(f"노트북에서 '{marker}' 를 포함한 셀을 찾지 못했습니다.")


def replace_dict_literal(src: str, name: str, new_body: str) -> str:
    """
    최상위 `name = { ... }` 딕셔너리 리터럴 전체를 new_body 로 교체.
    주석 처리된 정의(`# name = {`)는 건드리지 않는다.
    """
    lines = src.splitlines(keepends=True)
    start = None
    pattern = re.compile(rf"^{re.escape(name)}\s*=\s*\{{")
    for i, line in enumerate(lines):
        if pattern.match(line):
            start = i
            break
    if start is None:
        raise KeyError(f"'{name} = {{' 정의를 찾지 못했습니다.")

    depth = 0
    end = None
    for i in range(start, len(lines)):
        depth += lines[i].count("{") - lines[i].count("}")
        if depth <= 0:
            end = i
            break
    if end is None:
        raise ValueError(f"'{name}' 딕셔너리의 끝을 찾지 못했습니다.")

    replacement = new_body if new_body.endswith("\n") else new_body + "\n"
    return "".join(lines[:start]) + replacement + "".join(lines[end + 1:])


def replace_assignment(src: str, name: str, value_code: str) -> str:
    """최상위 `name = <한 줄>` 을 `name = value_code` 로 교체 (없으면 그대로)."""
    pattern = re.compile(rf"^{re.escape(name)}\s*=\s*.*$", re.MULTILINE)
    if not pattern.search(src):
        return src
    return pattern.sub(f"{name} = {value_code}", src, count=1)


def replace_attr_assignment(src: str, attr_path: str, value_code: str) -> str:
    """`mdl.parameters.timelimit = 300` 같은 속성 대입을 교체."""
    pattern = re.compile(
        rf"^(\s*){re.escape(attr_path)}\s*=\s*.*$", re.MULTILINE
    )
    if not pattern.search(src):
        return src
    return pattern.sub(rf"\g<1>{attr_path} = {value_code}", src, count=1)


def truncate_at(src: str, marker: str) -> str:
    """marker 가 처음 등장하는 줄부터 끝까지 잘라낸다."""
    lines = src.splitlines(keepends=True)
    for i, line in enumerate(lines):
        if marker in line:
            return "".join(lines[:i])
    return src


def py_repr(value: Any) -> str:
    return repr(value)


# ----------------------------------------------------------------------------
# 실행
# ----------------------------------------------------------------------------
# ----------------------------------------------------------------------------
# 취소 (Cancellation)
# ----------------------------------------------------------------------------
#: 사용자가 '취소'를 누르면 서버가 이 이벤트를 set 한다.
#: 실행은 한 번에 하나뿐이므로 모듈 전역 하나로 충분하다.
CANCEL = threading.Event()


class Cancelled(Exception):
    """사용자가 실행을 취소했다."""


def request_cancel() -> None:
    CANCEL.set()


def clear_cancel() -> None:
    CANCEL.clear()


def cancelled() -> bool:
    """
    노트북 네임스페이스에 `_cranebench_cancelled` 로 주입된다.

    Gurobi 콜백이 매 노드마다 이걸 확인하고 model.terminate() 를 부르기 때문에,
    긴 B&B 도중에도 즉시 멈출 수 있다.
    """
    return CANCEL.is_set()


@dataclass
class NBCell:
    """
    실행할 셀 하나.

    soft=True 인 셀은 실패해도 전체 실행을 중단하지 않는다.
    (예: 노트북의 matplotlib 시각화 셀 — 그림이 안 그려져도 해는 유효하다)
    """
    source: str
    soft: bool = False
    label: str = ""


@dataclass
class RunOutcome:
    ok: bool
    namespace: dict[str, Any]
    log: str
    error: str | None = None
    failed_cell: int | None = None
    failed_source: str | None = None
    wall_time: float = 0.0
    executed_cells: int = 0
    soft_errors: list[str] = field(default_factory=list)
    was_cancelled: bool = False


class _Tee(io.TextIOBase):
    """stdout 을 문자열 버퍼와 원래 stdout 양쪽으로 흘려보낸다."""

    def __init__(self, buffer: io.StringIO, mirror, limit: int = 4_000_000):
        self.buffer = buffer
        self.mirror = mirror
        self.limit = limit

    def write(self, s: str) -> int:
        if self.buffer.tell() < self.limit:
            self.buffer.write(s)
        if self.mirror is not None:
            try:
                self.mirror.write(s)
            except Exception:
                pass
        return len(s)

    def flush(self) -> None:
        if self.mirror is not None:
            try:
                self.mirror.flush()
            except Exception:
                pass


def execute_cells(
    cells: "list[str | NBCell]",
    prelude: str = "",
    mirror_stdout: bool = True,
    on_cell: Callable[[int, str], None] | None = None,
) -> RunOutcome:
    """
    셀들을 하나의 공유 네임스페이스에서 순서대로 실행한다.

    stdout 을 전역으로 가로채기 때문에, 동시에 두 개의 노트북이 실행되면
    로그가 섞인다. 그래서 실행 전체를 하나의 락으로 직렬화한다.
    """
    with _EXEC_LOCK:
        return _execute_cells(cells, prelude, mirror_stdout, on_cell)


_EXEC_LOCK = threading.Lock()


def _execute_cells(
    cells: "list[str | NBCell]",
    prelude: str = "",
    mirror_stdout: bool = True,
    on_cell: Callable[[int, str], None] | None = None,
) -> RunOutcome:
    # `_cranebench_cancelled` 를 미리 넣어 둔다. 어댑터가 주입하는 Gurobi
    # 콜백이 이 함수를 호출해 실행 중에도 중단할 수 있다.
    ns: dict[str, Any] = {"__name__": "__nb__", "_cranebench_cancelled": cancelled}
    buf = io.StringIO()
    # 현재 stdout 으로 흘려보낸다. 서버가 sys.stdout 을 LiveLog 로 돌려 두었다면
    # 그쪽으로 들어가 실시간 화면에 보이고, 그냥 실행 중이면 콘솔로 나간다.
    mirror = sys.stdout if mirror_stdout else None
    tee = _Tee(buf, mirror)

    t0 = time.time()
    executed = 0
    error = None
    failed_cell = None
    failed_source = None

    # matplotlib 이 창을 띄우지 않도록
    prelude_full = (
        "import matplotlib\n"
        "try:\n"
        "    matplotlib.use('Agg')\n"
        "except Exception:\n"
        "    pass\n"
    )
    if not matplotlib_status()[0]:
        prelude_full = ""
    prelude_full += prelude

    normalized = [c if isinstance(c, NBCell) else NBCell(c) for c in cells]
    if prelude_full.strip():
        normalized = [NBCell(prelude_full, label="prelude")] + normalized

    soft_errors: list[str] = []

    was_cancelled = False

    with redirect_stdout(tee), redirect_stderr(tee):
        for idx, cell in enumerate(normalized):
            if CANCEL.is_set():
                was_cancelled = True
                print("\n[취소] 사용자가 실행을 중단했습니다.")
                break
            if on_cell is not None:
                try:
                    on_cell(idx, cell.source)
                except Exception:
                    pass
            try:
                exec(compile(cell.source, f"<cell {idx}>", "exec"), ns)
                executed += 1
            except Cancelled:
                was_cancelled = True
                print("\n[취소] 사용자가 실행을 중단했습니다.")
                break
            except Exception:
                tb = traceback.format_exc()
                if cell.soft:
                    soft_errors.append(f"[{cell.label or idx}] {tb}")
                    print("\n[SOFT CELL FAILED] index=%d (%s)\n%s"
                          % (idx, cell.label, tb))
                    continue
                error = tb
                failed_cell = idx
                failed_source = cell.source
                print("\n[CELL FAILED] index=%d\n%s" % (idx, tb))
                break

    if CANCEL.is_set():
        was_cancelled = True

    return RunOutcome(
        ok=error is None,
        namespace=ns,
        log=buf.getvalue(),
        error=error,
        failed_cell=failed_cell,
        failed_source=failed_source,
        wall_time=time.time() - t0,
        executed_cells=executed,
        soft_errors=soft_errors,
        was_cancelled=was_cancelled,
    )


# ----------------------------------------------------------------------------
# 노트북이 그린 matplotlib 그림 회수
# ----------------------------------------------------------------------------
_MPL_STATE: tuple[bool, str] | None = None


def matplotlib_status() -> tuple[bool, str]:
    """
    matplotlib 을 쓸 수 있는지 한 번만 확인해 둔다.

    numpy 2.x 환경에 numpy 1.x 용으로 빌드된 matplotlib 이 깔려 있으면 import 할 때마다
    긴 경고와 traceback 을 쏟아낸다. 여기서 한 번 조용히 확인하고, 안 되면 차트를 건너뛴다.
    """
    global _MPL_STATE
    if _MPL_STATE is not None:
        return _MPL_STATE
    buf = io.StringIO()
    try:
        with redirect_stdout(buf), redirect_stderr(buf):
            import matplotlib
            matplotlib.use("Agg")
            import matplotlib.pyplot  # noqa: F401
        _MPL_STATE = (True, "")
    except Exception as exc:
        msg = str(exc).strip().splitlines()
        _MPL_STATE = (False, (msg[-1] if msg else repr(exc))[:300])
    return _MPL_STATE


def capture_figures(dpi: int = 110, max_figures: int = 6) -> list[str]:
    """
    현재 열려 있는 matplotlib figure 를 PNG(base64)로 뽑고 닫는다.

    노트북의 시각화 셀을 그대로 실행시킨 뒤 이 함수를 부르면,
    노트북에서 보던 것과 '같은 그림'을 웹에 그대로 실을 수 있다.
    """
    out: list[str] = []
    if not matplotlib_status()[0]:
        return out
    try:
        import base64
        import matplotlib.pyplot as plt
    except Exception:
        return out

    try:
        nums = plt.get_fignums()
    except Exception:
        return out

    for num in nums[:max_figures]:
        try:
            fig = plt.figure(num)
            buf = io.BytesIO()
            fig.savefig(buf, format="png", dpi=dpi, bbox_inches="tight",
                        facecolor="white")
            out.append(base64.b64encode(buf.getvalue()).decode("ascii"))
        except Exception:
            pass
    try:
        plt.close("all")
    except Exception:
        pass
    return out


def close_figures() -> None:
    if not matplotlib_status()[0]:
        return
    try:
        import matplotlib.pyplot as plt
        plt.close("all")
    except Exception:
        pass
