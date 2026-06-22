"""
Small compatibility boundary around bittensor imports.

Local scoring tests and mock runs should not fail just because the developer's
global Python has a Bittensor dependency conflict. Real miner/validator runs
still use the real bittensor package when it imports cleanly.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pydantic


class _FallbackLogging:
    def debug(self, msg: Any, *args: Any, **kwargs: Any) -> None:
        print(f"[debug] {msg}")

    def info(self, msg: Any, *args: Any, **kwargs: Any) -> None:
        print(f"[info] {msg}")

    def warning(self, msg: Any, *args: Any, **kwargs: Any) -> None:
        print(f"[warning] {msg}")

    def error(self, msg: Any, *args: Any, **kwargs: Any) -> None:
        print(f"[error] {msg}")


class _FallbackSynapse(pydantic.BaseModel):
    model_config = pydantic.ConfigDict(arbitrary_types_allowed=True, extra="allow")


def _missing_subtensor(*_: Any, **__: Any) -> None:
    raise RuntimeError("bittensor is not available in this Python environment")


def load_bittensor() -> Any:
    try:
        import bittensor as bt  # type: ignore

        return bt
    except Exception:
        return SimpleNamespace(
            Synapse=_FallbackSynapse,
            logging=_FallbackLogging(),
            subtensor=_missing_subtensor,
        )


bt = load_bittensor()
