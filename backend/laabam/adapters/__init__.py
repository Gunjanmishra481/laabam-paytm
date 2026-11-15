"""Adapters to the outside world: payments, Soundbox, SMS, lending and model providers.

Every real adapter has a sandbox twin. The registry picks one by name (per merchant or from
settings) and refuses sandbox-only implementations unless LAABAM_SANDBOX=1.
"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

KINDS = ("payments", "soundbox", "sms", "lending", "speech", "vision", "text")


class AdapterUnavailable(RuntimeError):
    pass


@dataclass(frozen=True)
class _Entry:
    factory: Callable[[], Any]
    sandbox_only: bool


class AdapterRegistry:
    def __init__(self, sandbox: bool) -> None:
        self.sandbox = sandbox
        self._entries: dict[tuple[str, str], _Entry] = {}
        self._instances: dict[tuple[str, str], Any] = {}

    def register(self, kind: str, name: str, factory: Callable[[], Any], sandbox_only: bool = False) -> None:
        if kind not in KINDS:
            raise ValueError(f"unknown adapter kind {kind!r}")
        self._entries[(kind, name)] = _Entry(factory, sandbox_only)
        self._instances.pop((kind, name), None)

    def names(self, kind: str) -> list[str]:
        return sorted(n for k, n in self._entries if k == kind)

    def get(self, kind: str, name: str) -> Any:
        entry = self._entries.get((kind, name))
        if entry is None:
            raise AdapterUnavailable(f"no {kind} adapter named {name!r}")
        if entry.sandbox_only and not self.sandbox:
            raise AdapterUnavailable(f"the {kind} adapter {name!r} needs LAABAM_SANDBOX=1")
        inst = self._instances.get((kind, name))
        if inst is None:
            inst = self._instances[(kind, name)] = entry.factory()
        return inst
