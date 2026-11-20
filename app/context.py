"""Per-run execution context passed explicitly to gateways (no hidden globals)."""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from app.security import Principal


def _noop(_event: dict) -> None:
    return None


@dataclass
class RunCtx:
    principal: Principal
    run_id: str | None = None
    node: str | None = None
    agent: str | None = None
    emit: Callable[[dict], Any] = _noop
    options: dict = field(default_factory=dict)
    origin: str = "live"

    @property
    def workspace_id(self) -> str:
        return self.principal.tenant

    @property
    def dry_run(self) -> bool:
        return bool(self.options.get("dry_run"))
