"""bench: one command from a repository to a table, resumable."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

ROOT = Path(__file__).resolve().parents[1]


def sh(cwd: Path, *args: str) -> None:
    subprocess.run(args, cwd=cwd, check=True, capture_output=True)


@pytest.fixture
def repo(tmp_path) -> Path:
    """One real fix in history: add div() with its tests."""
    root = tmp_path / "sample"
    root.mkdir()
    sh(root, "git", "init", "-q")
    sh(root, "git", "config", "user.email", "t@example.com")
    sh(root, "git", "config", "user.name", "T")
    (root / "tests").mkdir()
    (root / "calc.py").write_text("def add(a, b):\n    return a + b\n")
    (root / "tests" / "test_calc.py").write_text(
        "from calc import add\n\ndef test_add():\n    assert add(1, 2) == 3\n")
    sh(root, "git", "add", "-A")
    sh(root, "git", "commit", "-q", "-m", "initial")
    (root / "calc.py").write_text(
        "def add(a, b):\n    return a + b\n\n\ndef div(a, b):\n"
        "    if b == 0:\n        raise ValueError('division by zero')\n    return a / b\n")
    (root / "tests" / "test_calc.py").write_text(
        "import pytest\n\nfrom calc import add, div\n\n"
        "def test_add():\n    assert add(1, 2) == 3\n\n"
        "def test_div():\n    assert div(6, 3) == 2\n\n"
        "def test_div_by_zero():\n    with pytest.raises(ValueError):\n        div(1, 0)\n")
    sh(root, "git", "add", "-A")
    sh(root, "git", "commit", "-q", "-m", "Add div with zero check")
    return root


def bench(cwd: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, "-m", "groundhog", "bench", *args, "--no-color"],
                          cwd=cwd, capture_output=True, text=True,
                          env={**os.environ, "PYTHONPATH": str(ROOT)})


def test_needs_something_to_race(repo, tmp_path):
    proc = bench(tmp_path, str(repo))
    assert proc.returncode == 2
    assert "bench needs something to race" in proc.stderr
    assert "--models" in proc.stderr or "--agent" in proc.stderr


def test_end_to_end_then_resume(repo, tmp_path):
    # An agent that applies the real fix: `git checkout <sha> -- <source>` needs
    # the sha, which the prompt does not carry, so cheat via the task's own
    # test target: the test file names the function, the fix is one line.
    fix = ("python3 -c \"import re,pathlib; p=pathlib.Path('calc.py'); "
           "p.write_text(p.read_text()+'\\n\\ndef div(a, b):\\n    if b == 0:\\n"
           "        raise ValueError(\\\"division by zero\\\")\\n    return a / b\\n')\"")
    first = bench(tmp_path, str(repo), "--agent-cmd", fix, "--since", "10 years ago",
                  "--stability", "1", "--no-auto-env",
                  "--test-cmd", f"{sys.executable} -m pytest {{tests}} -x -q")
    assert first.returncode == 0, first.stderr
    out = tmp_path / ".groundhog" / repo.name
    for name in ("candidates.jsonl", "tasks.jsonl", "results.jsonl", "report.md", "index.html", "badge.json"):
        assert (out / name).exists(), name
    results = [json.loads(l) for l in (out / "results.jsonl").read_text().splitlines()]
    assert len(results) == 1 and results[0]["solved"], results
    assert "1. Mine" in first.stderr and "2. Validate" in first.stderr
    assert "| Model |" in (out / "report.md").read_text()
    assert json.loads((out / "badge.json").read_text())["message"].startswith("100%")

    second = bench(tmp_path, str(repo), "--agent-cmd", fix, "--no-auto-env",
                   "--test-cmd", f"{sys.executable} -m pytest {{tests}} -x -q")
    assert second.returncode == 0, second.stderr
    assert "already exists" in second.stderr
    assert "1. Mine" not in second.stderr
    assert "--resume" in second.stderr
    assert len((out / "results.jsonl").read_text().splitlines()) == 1



def test_report_step_forwards_no_color(monkeypatch, tmp_path):
    from groundhog import bench
    seen = []
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    monkeypatch.setattr(bench, "step", lambda title, cmd, colour: seen.append(cmd) or 0)
    monkeypatch.setattr(bench, "count", lambda path: 1)
    monkeypatch.setattr(bench.subprocess, "run", lambda *a, **k: None)
    monkeypatch.setattr(sys, "argv", ["bench", str(tmp_path), "--models", "ollama:x",
                                      "--out-dir", str(tmp_path / "out"), "--no-color"])
    (tmp_path / "out").mkdir()
    (tmp_path / "out" / "tasks.jsonl").write_text("{}\n")
    bench.main()
    report = next(cmd for cmd in seen if "report" in cmd)
    assert "--no-color" in report


@pytest.mark.parametrize("mode", [["--models", "ollama:x"], ["--agent", "claude-code"],
                                  ["--agent-cmd", "echo done"]])
@pytest.mark.parametrize("forward_options", [False, True])
def test_run_step_forwards_options(monkeypatch, tmp_path, mode, forward_options):
    from groundhog import bench
    seen = []
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    monkeypatch.setattr(bench, "step", lambda title, cmd, colour: seen.append(cmd) or 0)
    monkeypatch.setattr(bench, "count", lambda path: 1)
    monkeypatch.setattr(bench.subprocess, "run", lambda *a, **k: None)
    out = tmp_path / "out"
    out.mkdir()
    (out / "tasks.jsonl").write_text("{}\n")
    options = ["--commit-tests", "--no-patches", "--agent-timeout", "1800",
               "--sample", "0", "--seed", "0", "--system-prompt", str(tmp_path / "prompt file.md"),
               "--sandbox-deny", str(tmp_path / "other clone"),
               "--sandbox-deny", str(tmp_path / "cache")]
    monkeypatch.setattr(sys, "argv", ["bench", str(tmp_path), *mode,
                       "--out-dir", str(out), * (options if forward_options else [])])
    assert bench.main() == 0
    run = next(cmd for cmd in seen if "run" in cmd)
    for flag in ["--commit-tests", "--no-patches", "--agent-timeout", "--sample", "--seed",
                 "--system-prompt", "--sandbox-deny"]:
        assert (flag in run) == forward_options
    if forward_options:
        for flag, value in [("--agent-timeout", "1800"), ("--sample", "0"), ("--seed", "0"),
                            ("--system-prompt", str(tmp_path / "prompt file.md"))]:
            assert run[run.index(flag) + 1] == value
        denied = [run[i+1] for i, value in enumerate(run) if value == "--sandbox-deny"]
        assert denied == [str(tmp_path / "other clone"), str(tmp_path / "cache")]
        report = next(cmd for cmd in seen if "report" in cmd)
        assert not any(flag in report for flag in options if flag.startswith("--"))
