"""应用用例；具体实现按需导入，避免包初始化加载整个依赖图。"""

from importlib import import_module
from typing import Any

__all__ = [
    "BatchProcessUseCase",
    "ExportArticleUseCase",
    "FetchArticleUseCase",
    "SummarizeArticleUseCase",
]

_MODULES = {
    "BatchProcessUseCase": ".batch_process",
    "ExportArticleUseCase": ".export_article",
    "FetchArticleUseCase": ".fetch_article",
    "SummarizeArticleUseCase": ".summarize_article",
}


def __getattr__(name: str) -> Any:
    module_name = _MODULES.get(name)
    if module_name is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    return getattr(import_module(module_name, __name__), name)
