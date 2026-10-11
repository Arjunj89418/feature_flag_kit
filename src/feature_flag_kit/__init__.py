"""feature_flag_kit: evaluate feature flags with percentage rollout and overrides.

Public API:
    FeatureFlag   - immutable flag definition (percentage + overrides).
    FlagSet       - collection of FeatureFlag objects with bulk evaluation.
    evaluate      - free function to evaluate a single FeatureFlag for a key.
"""

from .core import FeatureFlag, FlagSet, evaluate

__all__ = ["FeatureFlag", "FlagSet", "evaluate"]
