"""Path A benchmark harness — measure before/after PTT latency with stats.

Usage
-----
1. Checkout the branch BEFORE Path A (e.g. `git checkout 4716260`) and record
   baseline interactions:

       py -3.13 -m tools.bench_path_a record --label before --n 20

   (Launch ``py -3.13 -m app`` in another terminal, do N real PTT presses
   with roughly the same question + same app, then return to the harness
   and press Enter. It scrapes ~/.clicky-windows/debug/ for the N most-
   recent folders.)

2. Checkout Path A (this branch) and record:

       py -3.13 -m tools.bench_path_a record --label after --n 20

3. Compare the two runs:

       py -3.13 -m tools.bench_path_a compare \\
           ~/.clicky-windows/bench/before.json \\
           ~/.clicky-windows/bench/after.json

The compare output shows per-metric median (P50) before + after, delta in
milliseconds, Mann-Whitney U one-sided p-value (H1: after is stochastically
smaller than before), and a 95% bootstrap confidence interval on the after-
median.

Why these stats
---------------
- **Mann-Whitney U (one-sided ``alternative='less'``)**: latency is right-
  skewed (long-tail), so a t-test on means is misleading. Mann-Whitney is
  rank-based / distribution-free and directly tests "after < before"
  stochastically. Reject null when p < 0.05.
- **Bootstrap CI on median**: percentile method, 9999 resamples. Gives an
  interpretable error-bar around the post-fix median without assuming
  Gaussianity. Report deltas WITH the CI.

See ROADMAP.md "Step 2 (Path A parallelism)" for the full context.
"""
from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from pathlib import Path

import numpy as np
from scipy import stats


BENCH_DIR = Path.home() / ".clicky-windows" / "bench"
DEBUG_DIR = Path.home() / ".clicky-windows" / "debug"


def mann_whitney_less(before: list[float], after: list[float]) -> tuple[float, float]:
    """One-sided Mann-Whitney U testing H1: after < before (stochastically).

    Returns ``(statistic, p_value)``. Reject null (no difference) when p < 0.05.
    Suitable for right-skewed latency data with no normality assumption.
    """
    before_arr = np.asarray(before, dtype=float)
    after_arr = np.asarray(after, dtype=float)
    result = stats.mannwhitneyu(after_arr, before_arr, alternative="less")
    return float(result.statistic), float(result.pvalue)


def bootstrap_median_ci(
    samples: list[float],
    confidence: float = 0.95,
    n_resamples: int = 9999,
) -> tuple[float, float]:
    """Bootstrap confidence interval on the median of ``samples``.

    Percentile method, ``n_resamples`` resamples (default 9999), fixed
    ``random_state=42`` for reproducibility. Returns ``(lower, upper)``.
    """
    samples_arr = np.asarray(samples, dtype=float)
    result = stats.bootstrap(
        (samples_arr,),
        statistic=np.median,
        confidence_level=confidence,
        n_resamples=n_resamples,
        method="percentile",
        random_state=42,
    )
    return float(result.confidence_interval.low), float(result.confidence_interval.high)


@dataclass
class MetricRow:
    """One metric's before/after pair + its computed summary."""
    name: str
    before: list[float]
    after: list[float]

    def summary(self) -> dict:
        before_p50 = float(np.median(self.before)) if self.before else float("nan")
        after_p50 = float(np.median(self.after)) if self.after else float("nan")
        delta = after_p50 - before_p50
        _, p = mann_whitney_less(self.before, self.after) if (
            self.before and self.after
        ) else (0.0, 1.0)
        ci_lo, ci_hi = bootstrap_median_ci(self.after) if self.after else (float("nan"),) * 2
        return {
            "name": self.name,
            "before_p50": before_p50,
            "after_p50": after_p50,
            "delta_ms": delta,
            "p_value": p,
            "ci_lo": ci_lo,
            "ci_hi": ci_hi,
        }


# --- Debug-log parsing -------------------------------------------------------

def _extract_timing(log_path: Path, marker: str, last: bool = False) -> float | None:
    """Extract the ``[+Xms]`` elapsed-time from the first (or last) log line
    containing ``marker``. Returns None if not found."""
    found_ms = None
    for line in log_path.read_text(encoding="utf-8", errors="replace").splitlines():
        if marker in line and line.startswith("[+") and "ms]" in line:
            try:
                ms = float(line[2:line.index("ms]")])
                if not last:
                    return ms
                found_ms = ms
            except ValueError:
                pass
    return found_ms


def _scrape_latest_n(n: int) -> dict:
    """Grab timings from the N most-recent debug folders."""
    folders = sorted(
        (p for p in DEBUG_DIR.iterdir() if p.is_dir()),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )[:n]

    metrics = {
        "stt_finalize_ms": [],
        "capture_end_ms": [],
        "claude_first_token_ms": [],
        "tts_called_ms": [],
    }
    for folder in folders:
        log_path = folder / "interaction.log"
        if not log_path.exists():
            continue
        stt_end = _extract_timing(log_path, "STT: final transcript")
        capture_end = _extract_timing(log_path, "CAPTURE:", last=True)
        claude_start = _extract_timing(log_path, "CLAUDE: streaming started")
        tts_start = _extract_timing(log_path, "TTS: ")

        if stt_end is not None:
            metrics["stt_finalize_ms"].append(stt_end)
        if capture_end is not None:
            metrics["capture_end_ms"].append(capture_end)
        if claude_start is not None:
            metrics["claude_first_token_ms"].append(claude_start)
        if tts_start is not None:
            metrics["tts_called_ms"].append(tts_start)
    return metrics


# --- CLI subcommands ---------------------------------------------------------

def record_cmd(label: str, n: int) -> None:
    """Prompt the user to run N real PTT interactions, then scrape ~/.clicky-windows/debug/."""
    print(f"Recording {n} PTT interactions labeled {label!r}.")
    print(f"  1. In another terminal, run: py -3.13 -m app")
    print(f"  2. Do {n} PTT presses (Ctrl+Alt+Space). Aim for the same question")
    print(f"     in the same app so interactions are comparable.")
    print(f"  3. Return here and press Enter.")
    input(f"Press Enter after completing {n} interactions... ")

    metrics = _scrape_latest_n(n)
    BENCH_DIR.mkdir(parents=True, exist_ok=True)
    out = BENCH_DIR / f"{label}.json"
    out.write_text(json.dumps(metrics, indent=2))

    print(f"\nWrote {out}")
    for k, v in metrics.items():
        print(f"  {k}: n={len(v)}, "
              f"median={float(np.median(v)) if v else float('nan'):.0f}ms")


def compare_cmd(before_path: str, after_path: str) -> None:
    """Print a table: metric, P50 before, P50 after, delta, p-value, 95% CI."""
    before = json.loads(Path(before_path).read_text())
    after = json.loads(Path(after_path).read_text())

    header = f"{'Metric':<30} {'Before P50':>12} {'After P50':>12} {'Δ (ms)':>10} {'p':>10} {'95% CI (after)':>22}"
    print(header)
    print("-" * len(header))

    for key in sorted(before.keys() | after.keys()):
        row = MetricRow(
            name=key,
            before=before.get(key, []),
            after=after.get(key, []),
        )
        s = row.summary()
        print(
            f"{s['name']:<30} "
            f"{s['before_p50']:>12.0f} "
            f"{s['after_p50']:>12.0f} "
            f"{s['delta_ms']:>+10.0f} "
            f"{s['p_value']:>10.4f} "
            f"[{s['ci_lo']:>6.0f}, {s['ci_hi']:>6.0f}]"
        )


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="tools.bench_path_a",
        description="Path A benchmark harness for Clicky Windows latency measurement.",
    )
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_record = sub.add_parser("record", help="Record N PTT interactions into a labeled JSON.")
    p_record.add_argument("--label", required=True, help="e.g. 'before' or 'after'")
    p_record.add_argument("--n", type=int, default=20, help="N interactions (default: 20)")

    p_compare = sub.add_parser("compare", help="Compare two labeled JSONs.")
    p_compare.add_argument("before", help="Path to the 'before' JSON")
    p_compare.add_argument("after", help="Path to the 'after' JSON")

    args = parser.parse_args()
    if args.cmd == "record":
        record_cmd(args.label, args.n)
    elif args.cmd == "compare":
        compare_cmd(args.before, args.after)


if __name__ == "__main__":
    main()
