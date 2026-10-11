"""Core feature-flag evaluation logic.

Design decisions (documented so the behaviour is predictable, not magical):

1. Percentage rollout is deterministic. For a given (flag_name, identifier)
   pair the result is always the same, so a user does not flip between
   cohorts across requests. We hash a composite string and reduce it to
   0-99; `bucket < percentage` means the flag is on.

2. We use hashlib.sha256 rather than Python's built-in `hash()`, because
   `hash()` is randomised per process (PYTHONHASHSEED) for strings —
   which would make rollout non-deterministic across restarts.

3. Overrides take precedence over percentage, regardless of order.
   If a key is in `enabled` it is on; if it is in `disabled` it is off;
   only otherwise does percentage apply.

4. If `enabled` and `disabled` both list the same key we raise
   `ValueError` at construction time rather than silently picking one —
   a contradictory config is a bug and should surface loudly.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from typing import Dict, FrozenSet, Iterable, Optional


@dataclass(frozen=True)
class FeatureFlag:
    """A single feature flag.

    Attributes:
        name:        Human-readable flag name. Also feeds the hash so that
                     two flags with the same percentage but different names
                     produce different cohorts.
        percentage:  Integer 0-100. 0 = off for everyone (except `enabled`),
                     100 = on for everyone (except `disabled`).
        enabled:     Identifiers that are always on, overriding percentage.
        disabled:    Identifiers that are always off, overriding percentage.
    """

    name: str
    percentage: int = 0
    enabled: FrozenSet[str] = field(default_factory=frozenset)
    disabled: FrozenSet[str] = field(default_factory=frozenset)

    def __post_init__(self) -> None:
        if not isinstance(self.name, str) or self.name == "":
            raise ValueError("name must be a non-empty string")
        if not isinstance(self.percentage, int) or isinstance(self.percentage, bool):
            raise TypeError("percentage must be an int")
        if not 0 <= self.percentage <= 100:
            raise ValueError("percentage must be between 0 and 100")
        conflict = self.enabled & self.disabled
        if conflict:
            raise ValueError(
                f"identifiers appear in both enabled and disabled: {sorted(conflict)}"
            )

    @staticmethod
    def _coerce_set(values: Optional[Iterable[str]]) -> FrozenSet[str]:
        if values is None:
            return frozenset()
        coerced: FrozenSet[str] = frozenset(values)
        return coerced

    @classmethod
    def create(
        cls,
        name: str,
        percentage: int = 0,
        *,
        enabled: Optional[Iterable[str]] = None,
        disabled: Optional[Iterable[str]] = None,
    ) -> "FeatureFlag":
        """Convenience factory that accepts any iterable for enabled/disabled.

        The dataclass itself requires frozensets because mutable defaults
        would be a footgun; this factory lets callers pass lists, tuples,
        or generators without thinking about it.
        """
        return cls(
            name=name,
            percentage=percentage,
            enabled=cls._coerce_set(enabled),
            disabled=cls._coerce_set(disabled),
        )


def _bucket(name: str, identifier: str) -> int:
    """Map (name, identifier) to a deterministic integer in [0, 99].

    Using sha256 gives a uniform-ish distribution and is stable across
    processes, unlike `hash()` which is salted per interpreter run.
    """
    raw = f"{name}::{identifier}".encode("utf-8")
    digest = hashlib.sha256(raw).digest()
    # Use the first 4 bytes as an unsigned 32-bit int, then mod 100.
    value = int.from_bytes(digest[:4], byteorder="big", signed=False)
    return value % 100


def evaluate(flag: FeatureFlag, identifier: str) -> bool:
    """Evaluate a flag for a given identifier.

    Order of precedence:
        1. `identifier` in `flag.disabled`  -> False
        2. `identifier` in `flag.enabled`   -> True
        3. `_bucket(flag.name, identifier) < flag.percentage` -> True/False

    Disabled is checked before enabled defensively, although the
    constructor forbids overlap, so the order only matters if someone
    subverts the dataclass — still, the intent is "disable wins."
    """
    if identifier in flag.disabled:
        return False
    if identifier in flag.enabled:
        return True
    return _bucket(flag.name, identifier) < flag.percentage


@dataclass(frozen=True)
class FlagSet:
    """A read-only bag of FeatureFlag objects keyed by name.

    Use this when you have many flags and want to look them up by name
    without scattering dicts around your application code.
    """

    _flags: Dict[str, FeatureFlag]

    @classmethod
    def from_iterable(cls, flags: Iterable[FeatureFlag]) -> "FlagSet":
        """Build a FlagSet from an iterable of FeatureFlag objects.

        Raises ValueError if two flags share the same name.
        """
        mapping: Dict[str, FeatureFlag] = {}
        for f in flags:
            if f.name in mapping:
                raise ValueError(f"duplicate flag name: {f.name!r}")
            mapping[f.name] = f
        return cls(_flags=mapping)

    def get(self, name: str) -> Optional[FeatureFlag]:
        """Return the flag with `name`, or None if absent."""
        return self._flags.get(name)

    def is_enabled(self, name: str, identifier: str) -> bool:
        """Evaluate the named flag for `identifier`.

        Returns False if the flag does not exist, rather than raising,
        because missing flags in a running system usually mean
        "feature off" — failing loud here would take down requests for
        a config typo. If you need to distinguish "missing" from "off",
        use `get()` and check for None.
        """
        flag = self._flags.get(name)
        if flag is None:
            return False
        return evaluate(flag, identifier)

    def evaluate_all(self, identifier: str) -> Dict[str, bool]:
        """Evaluate every flag for `identifier`, returning a name->bool dict."""
        return {name: evaluate(flag, identifier) for name, flag in self._flags.items()}

    def names(self) -> Iterable[str]:
        """Iterate over flag names."""
        return self._flags.keys()
