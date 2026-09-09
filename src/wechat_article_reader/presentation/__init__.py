"""
展示层

包含用户交互界面：
- web: Web 界面 (FastAPI + Jinja2)
- cli: 命令行界面 (Click + Rich)
"""

from typing import Any

__all__ = ["run_cli"]


def __getattr__(name: str) -> Any:
    if name == "run_cli":
        from .cli import run_cli

        return run_cli
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
