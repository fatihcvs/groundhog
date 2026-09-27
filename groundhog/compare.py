#!/usr/bin/env python3
"""Compare two Groundhog runs on the same task set.

This is the regression check: run before a change, run after, compare. It is
also how you run an ablation -- change one thing about the agent, hold the tasks
fixed, and see whether the difference survives a significance test.

Exits non-zero when a run has regressed, so it can gate CI.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from statistics import median

from .stats import mcnemar, outcomes, tasks_needed, wilson

BOLD, DIM, GREEN, RED, RESET = "\033[1m", "\033[2m", "\033[32m", "\033[31m", "\033[0m"


def load(path: Path, model: str | None = None) -> list[dict]:
    records = [json.loads(l) for l in path.read_text().splitlines() if l.strip()]
    if model:
        records = [r for r in records if r["model"] == model]
    return records


# (key, label, how to print it). Rates are shares of attempts; the rest are
# medians over attempts, so one runaway attempt does not move them.
BEHAVIOUR = [
    ("target_passed", "target tests passed", "pct"),
    ("broke_tests", "broke passing tests", "pct"),
    ("edited_tests", "edited a test", "pct"),
    ("errored", "harness error/timeout", "pct"),
    ("median_seconds", "median time", "secs"),
    ("median_files", "median files touched", "num"),
    ("median_calls", "median tool calls", "num"),
    ("tool_error_rate", "tool calls that errored", "pct"),
    ("median_first_edit", "median first-edit turn", "num"),
    ("from_text", "calls parsed from prose", "pct"),
]


def behaviour(records: list[dict]) -> dict[str, float | None]:
    """How the agent worked, not just whether it solved the task.

    A change in pass rate says that something moved; these say what. Tool-call
    figures exist only for the built-in loop -- an external agent's calls are
    invisible to the harness -- so they are None rather than a fake zero.
    """
    n = len(records)
    if not n:
        return {key: None for key, _, _ in BEHAVIOUR}

    def share(pred) -> float:
        return sum(1 for r in records if pred(r)) / n

    measured = [r["tools"] for r in records if (r.get("tools") or {}).get("calls")]
    calls = sum(t["calls"] for t in measured)
    first_edits = [t["first_edit_turn"] for t in measured if t.get("first_edit_turn") is not None]
    return {
        "target_passed": share(lambda r: r.get("fail_to_pass_passed")),
        "broke_tests": share(lambda r: (r.get("broke_pass_to_pass") or 0) > 0),
        "edited_tests": share(lambda r: r.get("tampered_with_tests")),
        # A test edit is recorded in `error` too; that is the agent failing, not the harness.
        "errored": share(lambda r: r.get("error") and not r.get("tampered_with_tests")),
        "median_seconds": median(r.get("seconds") or 0.0 for r in records),
        "median_files": median(len(r.get("files_touched") or []) for r in records),
        "median_calls": median(t["calls"] for t in measured) if measured else None,
        "tool_error_rate": sum(t.get("errors", 0) for t in measured) / calls if calls else None,
        "median_first_edit": median(first_edits) if first_edits else None,
        "from_text": sum(t.get("recovered_from_text", 0) for t in measured) / calls if calls else None,
    }


def why_failed(records: list[dict]) -> str:
    """The most telling reason a task's attempts failed, for the flip list."""
    def reason(r: dict) -> str | None:
        if r.get("tampered_with_tests"):
            return "edited a test"
        if "timed out" in (r.get("error") or ""):
            return "timed out"
        if (r.get("broke_pass_to_pass") or 0) > 0:
            target = "target passed" if r.get("fail_to_pass_passed") else "target failed"
            return f"{target}, broke {r['broke_pass_to_pass']} other test(s)"
        if r.get("error"):
            return f"error: {r['error'][:40]}"
        return None
    return next((why for why in map(reason, records) if why), "target tests still fail")


def flips(a: list[dict], b: list[dict]) -> tuple[list[tuple[str, str]], list[tuple[str, str]]]:
    """Tasks the change broke, and tasks it fixed, each with the losing side's reason."""
    oa, ob = outcomes(a), outcomes(b)
    by_task = lambda rs, t: [r for r in rs if r["task_id"] == t]
    subject = {r["task_id"]: r.get("subject", r["task_id"]) for r in a + b}
    lost = [(subject[t], why_failed(by_task(b, t))) for t in sorted(set(oa) & set(ob)) if oa[t] and not ob[t]]
    won = [(subject[t], why_failed(by_task(a, t))) for t in sorted(set(oa) & set(ob)) if ob[t] and not oa[t]]
    return lost, won


def fmt(value: float | None, kind: str) -> str:
    if value is None:
        return "-"
    return {"pct": f"{value:.0%}", "secs": f"{value:.0f}s"}.get(kind, f"{value:g}")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("baseline", type=Path, help="results from before the change")
    ap.add_argument("current", type=Path, help="results from after the change")
    ap.add_argument("--model", help="restrict both sides to one model")
    ap.add_argument("--label-a", default="baseline")
    ap.add_argument("--label-b", default="current")
    ap.add_argument("--fail-under", type=float, default=None,
                    help="exit 1 if the current pass rate falls below this (0-1)")
    ap.add_argument("--fail-on-regression", action="store_true",
                    help="exit 1 if the current run is significantly worse")
    ap.add_argument("--json", type=Path)
    ap.add_argument("--no-color", action="store_true", help="plain text; also honours NO_COLOR")
    cfg = ap.parse_args()
    colour = not cfg.no_color and not os.environ.get("NO_COLOR") and sys.stdout.isatty()
    bold, dim, green, red, reset = (BOLD, DIM, GREEN, RED, RESET) if colour else ("", "", "", "", "")

    for path in (cfg.baseline, cfg.current):
        if not path.exists():
            print(f"no results at {path}", file=sys.stderr)
            return 2

    a, b = load(cfg.baseline, cfg.model), load(cfg.current, cfg.model)
    if not a or not b:
        print("one side has no matching records", file=sys.stderr)
        return 2

    stamps_a = {r.get("manifest", "") for r in a}
    stamps_b = {r.get("manifest", "") for r in b}
    if stamps_a and stamps_b and stamps_a != stamps_b and all(stamps_a | stamps_b):
        print(f"\n{red}WARNING: these runs used different task sets "
              f"({', '.join(sorted(stamps_a))} vs {', '.join(sorted(stamps_b))}).{reset}")
        print(f"{dim}  Only shared task ids are compared, so the result below is "
              f"a smaller\n  comparison than it appears. Pin one task set and "
              f"re-run both arms.{reset}")

    result = mcnemar(a, b)
    lo_a, hi_a = wilson(round(result.a_rate * result.shared_tasks), result.shared_tasks)
    lo_b, hi_b = wilson(round(result.b_rate * result.shared_tasks), result.shared_tasks)
    delta = result.b_rate - result.a_rate

    print(f"\n{bold}{'':<12}{'PASS RATE':>11}{'95% CI':>18}{reset}")
    print(f"{cfg.label_a:<12}{result.a_rate:>10.0%}{f'{lo_a:.0%} – {hi_a:.0%}':>18}")
    print(f"{cfg.label_b:<12}{result.b_rate:>10.0%}{f'{lo_b:.0%} – {hi_b:.0%}':>18}")

    colour = green if delta > 0 else (red if delta < 0 else "")
    print(f"\n{colour}{'change':<12}{delta:>+10.0%}{reset}   over {result.shared_tasks} shared tasks")

    print(f"\n{bold}PAIRED TEST (McNemar, exact){reset}")
    print(f"  solved by {cfg.label_a} only:  {result.a_only}")
    print(f"  solved by {cfg.label_b} only:  {result.b_only}")
    print(f"  solved by both:  {result.both}    neither: {result.neither}")
    print(f"\n  {bold}{result.verdict(cfg.label_a, cfg.label_b)}{reset}")

    note = result.power_note()
    if note:
        print(f"\n  {dim}{note}{reset}")
    if abs(delta) > 0 and not result.significant:
        need = tasks_needed(result.a_rate, abs(delta) or 0.05)
        if need:
            print(f"  {dim}An unpaired study of this effect would need ~{need} tasks; "
                  f"you have {result.shared_tasks}.{reset}")

    shared = set(outcomes(a)) & set(outcomes(b))
    a_shared = [r for r in a if r["task_id"] in shared]
    b_shared = [r for r in b if r["task_id"] in shared]
    ba, bb = behaviour(a_shared), behaviour(b_shared)
    print(f"\n{bold}HOW THEY WORKED{reset}  {dim}per attempt, shared tasks only{reset}")
    print(f"{bold}{'':<26}{cfg.label_a:>12}{cfg.label_b:>12}{reset}")
    for key, label, kind in BEHAVIOUR:
        if ba[key] is None and bb[key] is None:
            continue
        print(f"{label:<26}{fmt(ba[key], kind):>12}{fmt(bb[key], kind):>12}")
    if ba["median_calls"] is None and bb["median_calls"] is None:
        print(f"{dim}  Tool-call figures are recorded by the built-in loop only; "
              f"external agents' calls are not visible to the harness.{reset}")

    lost, won = flips(a, b)
    for title, rows, colour in ((f"solved by {cfg.label_a}, not {cfg.label_b}", lost, red),
                                (f"solved by {cfg.label_b}, not {cfg.label_a}", won, green)):
        if not rows:
            continue
        print(f"\n{bold}{title}{reset}  {dim}(why the other side failed){reset}")
        for subj, why in rows[:10]:
            print(f"  {colour}{subj[:44]:<46}{reset}{dim}{why}{reset}")
        if len(rows) > 10:
            print(f"  {dim}... and {len(rows) - 10} more{reset}")

    payload = {
        "baseline_rate": round(result.a_rate, 4),
        "current_rate": round(result.b_rate, 4),
        "delta": round(delta, 4),
        "shared_tasks": result.shared_tasks,
        "p_value": round(result.p_value, 5),
        "significant": result.significant,
        "verdict": result.verdict(cfg.label_a, cfg.label_b),
        "behaviour": {"baseline": ba, "current": bb},
        "lost": [{"task": t, "why": w} for t, w in lost],
        "won": [{"task": t, "why": w} for t, w in won],
    }
    if cfg.json:
        cfg.json.parent.mkdir(parents=True, exist_ok=True)
        cfg.json.write_text(json.dumps(payload, indent=2))

    status = 0
    if cfg.fail_under is not None and result.b_rate < cfg.fail_under:
        print(f"\n{red}FAIL: pass rate {result.b_rate:.0%} is below the floor of "
              f"{cfg.fail_under:.0%}{reset}")
        status = 1
    if cfg.fail_on_regression and result.significant and result.b_rate < result.a_rate:
        print(f"\n{red}FAIL: significant regression (p = {result.p_value:.4f}){reset}")
        status = 1
    print()
    return status


if __name__ == "__main__":
    raise SystemExit(main())
