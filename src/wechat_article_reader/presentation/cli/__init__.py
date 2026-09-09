"""CLI 展示层。"""

from typing import Any

__all__ = ["cli", "run_cli"]


def __getattr__(name: str) -> Any:
    """延迟加载命令实现，支持 ``python -m ...cli.app`` 而不重复导入。"""
    if name in __all__:
        from .app import cli, run_cli

        return {"cli": cli, "run_cli": run_cli}[name]
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
