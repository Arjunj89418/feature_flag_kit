# feature_flag_kit

A small library for evaluating feature flags with percentage-based rollout and per-identifier overrides.

```python
from feature_flag_kit import FeatureFlag, FlagSet, evaluate

flag = FeatureFlag.create(
    "checkout-v2",
    percentage=30,
    enabled=["alice@example.com"],
    disabled=["banned@example.com"],
)

assert evaluate(flag, "alice@example.com") is True   # override
assert evaluate(flag, "banned@example.com") is False  # override
assert evaluate(flag, "random-user") in (True, False) # determined by hash

fs = FlagSet.from_iterable([flag])
assert fs.is_enabled("checkout-v2", "alice@example.com") is True
assert fs.evaluate_all("alice@example.com") == {"checkout-v2": True}
```

## Why this exists

The problem is simple: you have a feature, you want to turn it on for a percentage of users, and you need to override that percentage for specific identifiers (internal testers, banned users). You do not need a server, a dashboard, or an analytics pipeline — you need a function that returns a boolean and returns the same boolean next time.

The trade-off: this library is stateless and in-process. There is no admin UI, no audit log, no dynamic reconfiguration. You define flags in code (or load them from a config file you control) and evaluate them where your request lives. If you need to change a percentage you redeploy. That is deliberate: the complexity of a flag system grows where state lives, and keeping state out keeps the behaviour trivially testable.

## The awkward edge

Percentage rollout is deterministic but not uniform. For a given `(flag_name, identifier)` pair we hash a composite string with SHA-256 and reduce it to a bucket in `[0, 99]`. If the bucket is below the percentage, the flag is on. This means:

- The same user always sees the same result across processes and restarts (SHA-256 is stable; we do not use Python's `hash()`, which is salted per interpreter run).
- Distribution is approximately uniform but not perfectly so. A 50% flag over 10,000 users will not give exactly 5,000 enabled — expect a few hundred either way. Do not use this for experiments that require exact cohort sizes.

If an identifier appears in both `enabled` and `disabled`, the constructor raises `ValueError` rather than silently picking one. A contradictory flag configuration is a bug; surface it at construction, not at evaluation time.

`FlagSet.is_enabled` returns `False` for a missing flag name rather than raising. In a running system a missing flag usually means "feature off," and raising on every request for a config typo is the wrong default. If you need to distinguish "missing" from "off," call `FlagSet.get(name)` and check for `None`.

## Design notes

The window stores values eagerly rather than keeping running aggregates. Running
sums drift with floating point over long streams, and recomputing from a small
buffer is cheap enough that the drift is not worth the speed.

## Limitations

Values are coerced to floats, so very large integers lose precision. If you need
exact integer aggregates over a window, this is the wrong tool.

