"""
공유 서버 자원 제한
===================

랩실 규칙(1인당 코어 제한)을 코드 차원에서 강제하기 위한 값들.

EPYC 9754 는 256 스레드라, 제한을 걸지 않으면 솔버 하나가 서버 전체를 잡는다.
UI 에서 사용자가 더 큰 값을 넣더라도 서버가 여기서 잘라낸다.
"""

from __future__ import annotations

import os


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, "") or default)
    except ValueError:
        return default


#: 방법론 옵션의 기본 스레드 수 (랩실 규칙: 1인 16코어, 개인 PC 면 코어 수까지)
DEFAULT_THREADS = _env_int("CRANEBENCH_DEFAULT_THREADS", min(16, os.cpu_count() or 16))

#: 서버가 허용하는 최대 스레드 수. 실행 요청이 이보다 크면 잘린다.
MAX_THREADS = _env_int("CRANEBENCH_MAX_THREADS", 32)

#: 솔버 1회 실행이 쓸 수 있는 메모리 soft limit (GB)
DEFAULT_MEM_GB = _env_int("CRANEBENCH_DEFAULT_MEM_GB", 32)
MAX_MEM_GB = _env_int("CRANEBENCH_MAX_MEM_GB", 64)

#: 실행 시간 상한 (초). 무한정 점유 방지.
MAX_TIME_LIMIT = _env_int("CRANEBENCH_MAX_TIME_LIMIT", 7200)


def clamp_options(options: dict) -> tuple[dict, list[str]]:
    """
    실행 옵션을 서버 상한으로 잘라낸다.
    반환: (잘라낸 옵션, 사용자에게 보여줄 안내 문구들)
    """
    out = dict(options or {})
    notes: list[str] = []

    if "threads" in out:
        try:
            want = int(float(out["threads"] or 0))
        except (TypeError, ValueError):
            want = 0
        if want <= 0:
            out["threads"] = DEFAULT_THREADS
            notes.append(f"스레드 자동(0) → 랩실 기본값 {DEFAULT_THREADS} 코어로 설정했습니다.")
        elif want > MAX_THREADS:
            out["threads"] = MAX_THREADS
            notes.append(f"스레드 {want} → 서버 상한 {MAX_THREADS} 코어로 제한했습니다.")
        else:
            out["threads"] = want

    if "mem_limit_gb" in out:
        try:
            want = float(out["mem_limit_gb"] or 0)
        except (TypeError, ValueError):
            want = 0
        if want <= 0:
            out["mem_limit_gb"] = DEFAULT_MEM_GB
        elif want > MAX_MEM_GB:
            out["mem_limit_gb"] = MAX_MEM_GB
            notes.append(f"메모리 {want}GB → 서버 상한 {MAX_MEM_GB}GB 로 제한했습니다.")
        else:
            out["mem_limit_gb"] = want

    if "time_limit" in out:
        try:
            want = float(out["time_limit"] or 0)
        except (TypeError, ValueError):
            want = 0
        if want <= 0 or want > MAX_TIME_LIMIT:
            out["time_limit"] = MAX_TIME_LIMIT
            notes.append(f"제한시간 → 서버 상한 {MAX_TIME_LIMIT}초로 제한했습니다.")
        else:
            out["time_limit"] = want

    return out, notes


def summary() -> dict:
    return {
        "default_threads": DEFAULT_THREADS,
        "max_threads": MAX_THREADS,
        "default_mem_gb": DEFAULT_MEM_GB,
        "max_mem_gb": MAX_MEM_GB,
        "max_time_limit": MAX_TIME_LIMIT,
        "cpu_count": os.cpu_count(),
    }
