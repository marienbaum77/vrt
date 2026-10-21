"""Experiment runner:  python -m experiments.run {calibrate,analyze,stability,loadtest,plots,all}"""
from __future__ import annotations

import argparse
import sys
from typing import List, Optional


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(prog="experiments")
    ap.add_argument("what", choices=["evaluate", "calibrate", "analyze", "stability", "loadtest", "plots", "all"])
    ap.add_argument("--workers", type=int, default=2)
    ap.add_argument("--reuse-grid", action="store_true", help="reuse results/grid_calibration.npz")
    ap.add_argument("--reuse-scores", action="store_true", help="reuse results/scores.csv")
    a = ap.parse_args(argv)
    w = a.what
    if w in ("calibrate", "all"):
        from .calibrate import calibrate

        calibrate(a.workers, reuse=a.reuse_grid)
    if w in ("analyze", "evaluate", "all"):
        from .analyze import analyze

        analyze(a.workers, a.reuse_scores)
    if w in ("stability", "all"):
        from .stability import run as stability

        stability()
    if w in ("loadtest", "all"):
        from .loadtest import run as loadtest

        loadtest()
    if w in ("plots", "all"):
        from .plots import make_all

        make_all()
    return 0


if __name__ == "__main__":
    sys.exit(main())
