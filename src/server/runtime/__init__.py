from __future__ import annotations


def get_runtime_context():
    from src.server.runtime.factory import get_runtime_context as _get_runtime_context

    return _get_runtime_context()


def reset_runtime_context() -> None:
    from src.server.runtime.factory import reset_runtime_context as _reset_runtime_context

    _reset_runtime_context()
