"""Tests for feature_flag_kit.core.

We assert behaviour that the implementation actually claims to support:
- deterministic percentage bucketing via sha256
- override precedence (enabled / disabled)
- construction-time validation
- FlagSet lookup and evaluation

We never assert on wall-clock time, never sleep, and never compare floats.
"""

import unittest

from feature_flag_kit import FeatureFlag, FlagSet, evaluate
from feature_flag_kit.core import _bucket


class TestFeatureFlagConstruction(unittest.TestCase):
    def test_defaults(self):
        f = FeatureFlag(name="checkout-v2")
        self.assertEqual(f.percentage, 0)
        self.assertEqual(f.enabled, frozenset())
        self.assertEqual(f.disabled, frozenset())

    def test_create_accepts_lists(self):
        f = FeatureFlag.create(
            "beta", 50, enabled=["alice", "bob"], disabled=("carol",)
        )
        self.assertIsInstance(f.enabled, frozenset)
        self.assertIsInstance(f.disabled, frozenset)
        self.assertIn("alice", f.enabled)
        self.assertIn("carol", f.disabled)

    def test_empty_name_rejected(self):
        with self.assertRaises(ValueError):
            FeatureFlag(name="")

    def test_percentage_below_zero_rejected(self):
        with self.assertRaises(ValueError):
            FeatureFlag(name="x", percentage=-1)

    def test_percentage_above_hundred_rejected(self):
        with self.assertRaises(ValueError):
            FeatureFlag(name="x", percentage=101)

    def test_percentage_bool_rejected(self):
        # bool is a subclass of int in Python; we forbid it explicitly so
        # FeatureFlag(name="x", percentage=True) is not silently treated
        # as percentage=1.
        with self.assertRaises(TypeError):
            FeatureFlag(name="x", percentage=True)

    def test_conflict_between_enabled_and_disabled(self):
        with self.assertRaises(ValueError):
            FeatureFlag(
                name="x",
                percentage=50,
                enabled=frozenset({"alice"}),
                disabled=frozenset({"alice"}),
            )


class TestBucket(unittest.TestCase):
    def test_bucket_in_range(self):
        for i in range(1000):
            b = _bucket("flag", f"user-{i}")
            self.assertGreaterEqual(b, 0)
            self.assertLess(b, 100)

    def test_bucket_deterministic(self):
        a = _bucket("flag", "user-42")
        b = _bucket("flag", "user-42")
        self.assertEqual(a, b)

    def test_different_flag_names_give_different_buckets(self):
        # Not guaranteed for every pair, but for these specific inputs we
        # assert they differ, which demonstrates that the flag name feeds
        # the hash. If this ever breaks due to a hash collision, pick a
        # different fixture rather than weakening the assertion.
        b1 = _bucket("flag-one", "user-1")
        b2 = _bucket("flag-two", "user-1")
        self.assertNotEqual(b1, b2)


class TestEvaluate(unittest.TestCase):
    def test_percentage_zero_is_off_for_everyone(self):
        f = FeatureFlag(name="off", percentage=0)
        for i in range(100):
            self.assertFalse(evaluate(f, f"u{i}"))

    def test_percentage_hundred_is_on_for_everyone(self):
        f = FeatureFlag(name="on", percentage=100)
        for i in range(100):
            self.assertTrue(evaluate(f, f"u{i}"))

    def test_enabled_override_beats_percentage(self):
        f = FeatureFlag(name="f", percentage=0, enabled=frozenset({"vip"}))
        self.assertTrue(evaluate(f, "vip"))
        self.assertFalse(evaluate(f, "random"))

    def test_disabled_override_beats_percentage(self):
        f = FeatureFlag(name="f", percentage=100, disabled=frozenset({"banned"}))
        self.assertFalse(evaluate(f, "banned"))
        self.assertTrue(evaluate(f, "random"))

    def test_disabled_beats_enabled_when_both_present_via_subclass(self):
        # Constructor forbids overlap, so we cannot test this through the
        # normal path. Instead we verify the evaluate() function's stated
        # precedence directly by crafting a flag where disabled contains
        # an identifier that percentage would also enable, and enabled
        # does NOT contain that identifier. This confirms disabled wins
        # over percentage without needing a contradictory config.
        #
        # (The "disabled beats enabled" path is unreachable in practice
        # because the constructor rejects it; we do not test unreachable
        # code. This test documents the precedence we actually rely on.)
        f = FeatureFlag(name="precedence", percentage=100, disabled=frozenset({"x"}))
        self.assertFalse(evaluate(f, "x"))

    def test_evaluating_same_key_twice_is_stable(self):
        f = FeatureFlag(name="stable", percentage=50)
        first = evaluate(f, "user-7")
        second = evaluate(f, "user-7")
        self.assertEqual(first, second)

    def test_50_percent_splits_reasonably(self):
        # We do not assert an exact count (hash distribution is not
        # perfectly uniform), but 10,000 keys at 50% should land between
        # 45% and 55%. This is a sanity check, not a mathematical law.
        f = FeatureFlag(name="split", percentage=50)
        enabled = sum(1 for i in range(10000) if evaluate(f, f"k{i}"))
        self.assertGreater(enabled, 4500)
        self.assertLess(enabled, 5500)


class TestFlagSet(unittest.TestCase):
    def test_from_iterable_and_get(self):
        a = FeatureFlag(name="a", percentage=0)
        b = FeatureFlag(name="b", percentage=100)
        fs = FlagSet.from_iterable([a, b])
        self.assertIs(fs.get("a"), a)
        self.assertIsNone(fs.get("missing"))

    def test_duplicate_name_rejected(self):
        a = FeatureFlag(name="dup", percentage=0)
        b = FeatureFlag(name="dup", percentage=50)
        with self.assertRaises(ValueError):
            FlagSet.from_iterable([a, b])

    def test_is_enabled_missing_flag_returns_false(self):
        fs = FlagSet.from_iterable([])
        self.assertFalse(fs.is_enabled("nope", "user"))

    def test_is_enabled_evaluates_flag(self):
        f = FeatureFlag(name="f", percentage=100, disabled=frozenset({"banned"}))
        fs = FlagSet.from_iterable([f])
        self.assertTrue(fs.is_enabled("f", "alice"))
        self.assertFalse(fs.is_enabled("f", "banned"))

    def test_evaluate_all(self):
        on = FeatureFlag(name="on", percentage=100)
        off = FeatureFlag(name="off", percentage=0)
        fs = FlagSet.from_iterable([on, off])
        result = fs.evaluate_all("user-1")
        self.assertEqual(result, {"on": True, "off": False})

    def test_names(self):
        a = FeatureFlag(name="alpha")
        b = FeatureFlag(name="beta")
        fs = FlagSet.from_iterable([a, b])
        self.assertEqual(set(fs.names()), {"alpha", "beta"})


class TestFrozenness(unittest.TestCase):
    def test_flag_is_frozen(self):
        f = FeatureFlag(name="x", percentage=10)
        with self.assertRaises(Exception):
            f.percentage = 20  # type: ignore[misc]


if __name__ == "__main__":
    unittest.main()
