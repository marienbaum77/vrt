"""Orchestration: configuration, baseline store, capture -> compare -> report pipeline, CLI."""
from .config import ProjectConfig, Scenario, load_config, parse_config
from .pipeline import EXIT_DIFF, EXIT_ERROR, EXIT_OK, RunOptions, compare_pair, run_baseline, run_test
from .profiles import get_profile, profile_names
from .store import BaselineStore, PageKey

__all__ = [
    "BaselineStore",
    "EXIT_DIFF",
    "EXIT_ERROR",
    "EXIT_OK",
    "PageKey",
    "ProjectConfig",
    "RunOptions",
    "Scenario",
    "compare_pair",
    "get_profile",
    "load_config",
    "parse_config",
    "profile_names",
    "run_baseline",
    "run_test",
]
