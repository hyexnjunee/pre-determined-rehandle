"""
방법론 레지스트리
=================

새 모델 노트북을 추가하려면 app/methods/ 에 NotebookModel 을 상속한 어댑터를 만들고
아래 목록에 한 줄 추가한다. 홈 카드·비교표·Gantt·논문 표가 자동으로 붙는다.

순서가 중요하다: 비교군을 먼저 두어야 개선율이 비교군 기준으로 계산된다.
"""

from __future__ import annotations

import os

from .methods.base import Method
from .methods.baseline import BaselineRehandle
from .methods.coop import CoopRehandle
from .methods.window_rule import WindowRule, WindowRuleV2
from .methods.rolling import rolling_methods


def build_registry(notebook_dir: str) -> dict[str, Method]:
    methods: list[Method] = [
        BaselineRehandle(notebook_dir),
        CoopRehandle(notebook_dir),
        WindowRule(),
        WindowRuleV2(),
    ]
    methods += rolling_methods(methods)
    return {m.key: m for m in methods}


def default_notebook_dir(base_dir: str) -> str:
    """web/ 옆의 model/ 폴더 (Pre-Determined_Rehandle/model)."""
    return os.path.normpath(os.path.join(base_dir, "..", "model"))
