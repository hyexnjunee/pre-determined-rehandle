"""
실시간 로그 (Live log)
======================

실행 중인 솔버의 출력을 웹 화면에 그대로 흘려보내기 위한 버퍼.

두 갈래의 출력을 하나로 모은다.

  1. **파이썬 출력** — 노트북 셀의 print, 제약식 개수, 등가성 경고 등.
     `sys.stdout` 을 이 객체로 돌려서 받는다.
  2. **솔버가 파일에 쓰는 로그** — Gurobi 의 B&B 로그는 C 레벨에서 출력되어
     파이썬 리다이렉트로 잡히지 않는다. 그래서 `LogFile` 파라미터로 파일에
     쓰게 하고, 여기서 그 파일의 새로 늘어난 부분을 주기적으로 읽어 붙인다.

클라이언트는 지금까지 읽은 오프셋을 들고 폴링하고, 서버는 그 이후에 늘어난
부분만 돌려준다. 매번 전체 로그를 보내지 않으므로 로그가 길어져도 가볍다.

동시 실행은 하나뿐이므로(JobManager 의 run_lock) 모듈 전역 CURRENT 하나로 충분하다.
"""

from __future__ import annotations

import io
import os
import threading

#: 버퍼 상한. 넘으면 앞쪽을 버린다 (오프셋은 계속 증가).
MAX_CHARS = 2_000_000


class LiveLog:
    def __init__(self, mirror=None):
        self._chunks: list[str] = []
        self._len = 0                 # 지금까지 들어온 전체 길이 (버린 것 포함)
        self._dropped = 0             # 버린 앞부분 길이
        self._lock = threading.Lock()
        self._files: list[dict] = []  # [{'path','pos'}]
        self.mirror = mirror          # 서버 콘솔에도 그대로 보이게

    # -- 파이썬 출력 수신 (파일 객체 인터페이스) --------------------------
    def write(self, s: str) -> int:
        if not s:
            return 0
        with self._lock:
            self._chunks.append(s)
            self._len += len(s)
            self._trim()
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

    def isatty(self) -> bool:
        return False

    # -- 솔버 로그 파일 붙이기 -------------------------------------------
    def attach_file(self, path: str) -> None:
        with self._lock:
            if not any(f["path"] == path for f in self._files):
                self._files.append({"path": path, "pos": 0})

    def pump(self) -> None:
        """붙여 둔 파일에서 새로 늘어난 부분을 읽어 버퍼에 넣는다."""
        for f in list(self._files):
            try:
                if not os.path.exists(f["path"]):
                    continue
                size = os.path.getsize(f["path"])
                if size <= f["pos"]:
                    continue
                with open(f["path"], "r", encoding="utf-8", errors="replace") as fh:
                    fh.seek(f["pos"])
                    new = fh.read()
                f["pos"] = size
            except OSError:
                continue
            if new:
                with self._lock:
                    self._chunks.append(new)
                    self._len += len(new)
                    self._trim()

    # -- 읽기 -------------------------------------------------------------
    def read_since(self, offset: int) -> tuple[str, int]:
        """offset 이후에 늘어난 부분과 새 offset 을 돌려준다."""
        self.pump()
        with self._lock:
            text = "".join(self._chunks)
            start = max(0, int(offset) - self._dropped)
            if start > len(text):
                start = len(text)
            return text[start:], self._dropped + len(text)

    @property
    def length(self) -> int:
        with self._lock:
            return self._len

    def text(self) -> str:
        self.pump()
        with self._lock:
            return "".join(self._chunks)

    # -- 내부 -------------------------------------------------------------
    def _trim(self) -> None:
        total = sum(len(c) for c in self._chunks)
        while total > MAX_CHARS and len(self._chunks) > 1:
            drop = self._chunks.pop(0)
            total -= len(drop)
            self._dropped += len(drop)


# ---------------------------------------------------------------------------
# 지금 실행 중인 로그 (어댑터가 솔버 로그 파일을 붙일 때 쓴다)
# ---------------------------------------------------------------------------
CURRENT: LiveLog | None = None


def set_current(live: LiveLog | None) -> None:
    global CURRENT
    CURRENT = live


def attach_file(path: str) -> None:
    """어댑터에서 호출: 이 솔버 로그 파일을 실시간 화면에 함께 보여준다."""
    if CURRENT is not None:
        CURRENT.attach_file(path)
