"""`compare` says what changed between two runs, not only whether it did."""

from __future__ import annotations

import json
import os

import pytest
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from groundhog import compare  # noqa: E402


def attempt(task, solved, *, target=None, broke=0, tampered=False, error="", seconds=10.0,
            files=("src/a.py",), tools=None):
    return {
        "model": "m", "task_id": task, "subject": f"Fix {task}", "solved": solved,
        "fail_to_pass_passed": solved if target is None else target,
        "broke_pass_to_pass": broke, "tampered_with_tests": tampered, "error": error,
        "seconds": seconds, "files_touched": list(files), "tools": tools or {},
    }


def tools(calls, errors=0, first_edit=3, from_text=0):
    return {"calls": calls, "errors": errors, "first_edit_turn": first_edit,
            "recovered_from_text": from_text}


class TestBehaviour:
    def test_rates_are_shares_of_attempts(self):
        rows = [attempt("t1", True), attempt("t2", False, target=True, broke=2),
                attempt("t3", False, tampered=True, target=True), attempt("t4", False, error="timeout")]
        b = compare.behaviour(rows)
        assert b["target_passed"] == 0.75
        assert b["broke_tests"] == 0.25
        assert b["edited_tests"] == 0.25
        assert b["errored"] == 0.25

    def test_a_test_edit_is_not_a_harness_error(self):
        # run.py writes "modified test files: ..." into `error` as well.
        rows = [attempt("t1", False, tampered=True, error="modified test files: tests/t.py")]
        b = compare.behaviour(rows)
        assert b["edited_tests"] == 1.0 and b["errored"] == 0.0

    def test_tool_figures_from_the_builtin_loop(self):
        rows = [attempt("t1", True, tools=tools(10, errors=1, first_edit=2)),
                attempt("t2", False, tools=tools(30, errors=5, first_edit=6, from_text=30))]
        b = compare.behaviour(rows)
        assert b["median_calls"] == 20
        assert b["tool_error_rate"] == 6 / 40
        assert b["median_first_edit"] == 4
        assert b["from_text"] == 30 / 40

    def test_external_agents_get_none_not_zero(self):
        b = compare.behaviour([attempt("t1", True), attempt("t2", False)])
        assert b["median_calls"] is None and b["tool_error_rate"] is None
        assert b["median_seconds"] == 10.0


class TestFlips:
    def test_each_flip_carries_the_losing_sides_reason(self):
        a = [attempt("t1", True), attempt("t2", False), attempt("t3", True)]
        b = [attempt("t1", False, target=True, broke=3), attempt("t2", True),
             attempt("t3", False, tampered=True)]
        lost, won = compare.flips(a, b)
        assert lost == [("Fix t1", "target passed, broke 3 other test(s)"), ("Fix t3", "edited a test")]
        assert won == [("Fix t2", "target tests still fail")]

    def test_a_timeout_is_named_and_a_failed_target_is_not_called_passed(self):
        a = [attempt("t1", True), attempt("t2", True)]
        b = [attempt("t1", False, target=False, broke=13, error="agent timed out after 900s"),
             attempt("t2", False, target=False, broke=2)]
        lost, _ = compare.flips(a, b)
        assert lost == [("Fix t1", "timed out"), ("Fix t2", "target failed, broke 2 other test(s)")]


def test_cli_prints_behaviour_and_writes_it_to_json(tmp_path):
    before = [attempt("t1", True, tools=tools(8)), attempt("t2", False, tools=tools(20, errors=10))]
    after = [attempt("t1", False, target=True, broke=1, tools=tools(9)), attempt("t2", True, tools=tools(12))]
    pa, pb, out = tmp_path / "a.jsonl", tmp_path / "b.jsonl", tmp_path / "c.json"
    pa.write_text("".join(json.dumps(r) + "\n" for r in before))
    pb.write_text("".join(json.dumps(r) + "\n" for r in after))
    done = subprocess.run([sys.executable, "-m", "groundhog", "compare", str(pa), str(pb), "--json", str(out)],
                          capture_output=True, text=True, cwd=Path(__file__).resolve().parents[1])
    assert done.returncode == 0, done.stderr
    assert "HOW THEY WORKED" in done.stdout and "tool calls that errored" in done.stdout
    payload = json.loads(out.read_text())
    assert payload["behaviour"]["current"]["broke_tests"] == 0.5
    assert payload["lost"] == [{"task": "Fix t1", "why": "target passed, broke 1 other test(s)"}]
    assert payload["won"] == [{"task": "Fix t2", "why": "target tests still fail"}]


@pytest.mark.parametrize("args,no_color,tty,expected_color", [
    ([], None, False, False),
    (["--no-color"], None, True, False),
    ([], "1", True, False),
    ([], "", True, True),
    ([], None, True, True),
])
def test_cli_color_controls(tmp_path, args, no_color, tty, expected_color):
    before = tmp_path / "before.jsonl"
    after = tmp_path / "after.jsonl"
    before.write_text(json.dumps(attempt("t1", True)) + "\n")
    after.write_text(json.dumps(attempt("t1", False)) + "\n")
    env = {k: v for k, v in os.environ.items() if k != "NO_COLOR"}
    if no_color is not None:
        env["NO_COLOR"] = no_color
    script = ("import sys; from groundhog.compare import main; "
              f"sys.stdout.isatty = lambda: {tty!r}; "
              "raise SystemExit(main())")
    done = subprocess.run([sys.executable, "-c", script, str(before), str(after),
                           "--fail-under", "0.5", *args],
                          capture_output=True, text=True, env=env,
                          cwd=Path(__file__).resolve().parents[1])
    assert done.returncode == 1, done.stderr
    assert "PAIRED TEST" in done.stdout
    assert "FAIL: pass rate" in done.stdout
    assert ("\x1b[" in done.stdout) == expected_color
