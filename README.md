# Groundhog

[![tests](https://github.com/abhinavsv3/groundhog/actions/workflows/test.yml/badge.svg)](https://github.com/abhinavsv3/groundhog/actions/workflows/test.yml)
[![PyPI](https://img.shields.io/pypi/v/groundhog-eval?label=groundhog-eval)](https://pypi.org/project/groundhog-eval/)
[![study](https://img.shields.io/badge/study-174%20attempts%2C%20%240-orange)](https://scalingthoughts.com/groundhog/study.html)
[![license](https://img.shields.io/badge/license-Apache--2.0-blue)](LICENSE)

**Change your coding agent without guessing.**

Groundhog turns past bugs and features from your git history into replayable
tests for your coding agent. Switch the model, rewrite the prompt, add a tool
or an MCP server, then run the same real tasks before and after: Groundhog
tells you what got better, what got worse, and what broke.

```
before   Claude Code + Sonnet 5    36 real tasks, 7 repos    36/36 solved
after    Claude Code + Haiku 4.5   the same 36 tasks         34/36 solved

         2 tasks got worse, 0 got better: too few to call a real drop
         attrs: passed its tests, broke 2 others   ← the one to read
         jinja: ran out of time
         Haiku cost 28% less, not the 50% its price suggests
```

That is a real run, not a mock-up ([`study/casestudy/`](study/casestudy/)).
A fix only counts if the agent didn't fix one thing by breaking another, and
didn't touch the tests. A harness that only checks the task's own tests
would have scored that attrs attempt as solved.

## What an answer looks like

The question above, *can we switch Claude Code from Sonnet 5 to the cheaper
Haiku 4.5?*, on 36 tasks from seven open-source repositories, with the agent
sandboxed so it can't look up the answer. Here is `groundhog compare`'s
output:

```
              PASS RATE            95% CI
sonnet-5          100%        90% – 100%
haiku-4.5          94%         82% – 98%

PAIRED TEST (McNemar, exact)
  solved by sonnet-5 only:  2
  solved by haiku-4.5 only:  0
  solved by both:  34    neither: 0

  no detectable difference (p = 0.500)

HOW THEY WORKED               sonnet-5   haiku-4.5
target tests passed               100%         97%
broke passing tests                 0%          6%
median time                        69s         77s

solved by sonnet-5, not haiku-4.5  (why the other side failed)
  Expose converter as a decorator (#1541)       target passed, broke 2 other test(s)
  test on trio, fix all missing aclose related  timed out
```

In plain words: both setups got the same 34 tasks right, and only two tasks
differ, which isn't enough to tell a real drop from luck. So the answer is
"no clear drop, and here are the two tasks to look at". The statistics sit
underneath (a paired test and intervals, so small differences aren't sold as
results), but the question is yours. The case study also caught a mistake in
Groundhog itself, which a follow-up run confirmed and fixed; the write-up has
both.

## See it in ten seconds

No API key, no network, nothing installed but [uv](https://docs.astral.sh/uv/):

```bash
uvx groundhog-eval demo
```

That builds a repository with real history, mines tasks from it, rejects a
refactor because its tests passed without the fix, and runs four scripted
agents. One applies the real fix. One applies the fix and breaks another
module. One applies the fix and edits the test. One does nothing. Only the
first scores. Captured output: [examples/demo-output.txt](examples/demo-output.txt).

## Check a change to your agent

```bash
uv tool install groundhog-eval          # or: pip install groundhog-eval
groundhog doctor                        # what this machine can run

# 1. Baseline: mine, validate and race your current setup.
groundhog bench ~/src/myrepo --agent claude-code

# 2. Change one thing, then race the same pinned tasks again.
groundhog run ~/src/myrepo --tasks .groundhog/myrepo/tasks.jsonl \
    --agent-cmd 'claude -p {prompt} --permission-mode bypassPermissions --model claude-haiku-4-5' \
    --models after --out .groundhog/myrepo/after.jsonl

# 3. Did it help or hurt?
groundhog compare .groundhog/myrepo/results.jsonl .groundhog/myrepo/after.jsonl --fail-on-regression
```

`bench` does mining, validation, the race and the report in one go, under
`.groundhog/<repo>/`; the second invocation skips straight to the race, and a
URL works anywhere a path does. Pinning `tasks.jsonl` is what makes the
comparison fair: both arms see exactly the same tasks, and `compare` warns if
they did not.

What you can change is whatever your agent command takes: the model, flags,
allowed tools, MCP config, an appended system prompt. To test new
instructions, pass them as a file outside the repository, for example
`--append-system-prompt-file ~/agent-configs/new-rules.md` for Claude Code.
Instruction files *inside* the repository (`CLAUDE.md`, `AGENTS.md`) come
from each task's own commit, like the rest of the code.

Because both runs use the same tasks, `compare` only has to look at the
tasks where the two setups disagree, which is why 30 to 50 tasks are enough
to catch a real regression. It shows what changed in how the agent worked
(tests it broke, tests it edited, time taken, and for Groundhog's own tool
loop, tool calls and errors) and every task that flipped, with why the losing
side failed. On 36 tasks, expect to catch big regressions, not small ones;
`--repeats` runs each task several times when you need more.

## In CI

```yaml
- uses: abhinavsv3/groundhog@v1
  with:
    tasks: tasks/pinned.jsonl
    baseline: results/baseline.jsonl
    agent: claude-code
  env:
    ANTHROPIC_API_KEY: ${{ secrets.ANTHROPIC_API_KEY }}
```

Runs when your agent config changes, writes the table to the job summary,
fails on a significant regression. [docs/ci.md](docs/ci.md) covers pinning a
task set, why the test is paired, and how to keep the cost sane.

## Why the scoring is strict

Here is a real attempt from [the study](study/REPORT.md): the task was "add
`count_words()`", the agent was qwen2.5-coder:7b, and the target test passed.

```diff
--- a/toolbox/text.py
+++ b/toolbox/text.py
@@ -1,62 +1,2 @@
-"""Small text helpers."""
-
-def clamp(value, low, high): ...
-def chunk(items, size): ...
-def dedupe(items): ...
-def truncate(value, length, suffix="..."): ...
-def parse_bool(value): ...
-def strip_prefix(value, prefix): ...
-def flatten(nested): ...
-def slugify(value): ...
-def safe_divide(numerator, denominator, default=0.0): ...
+def count_words(text):
+    return len(text.split())
```

One tool call, no read first. `count_words` is correct. Nine other functions
are gone. A harness that checks only the target test calls this a solve.
Across 174 attempts by five local models, that is what it looks like per model:

```
MODEL                       F2P-ONLY    TRUE  OVERSTATED  COLLATERAL
openai:qwen3:8b                17/38   17/38           0           0
openai:qwen2.5-coder:14b        9/22    8/22       +5pts   1 broke 1
openai:qwen2.5-coder:7b         6/38    3/38       +8pts  3 broke 14
openai:llama3.1:latest          2/38    1/38       +3pts   1 broke 1
```

Every patch behind those numbers is in [`study/patches/`](study/patches/),
and `groundhog show 87eb7a4e9b0c` prints the one above. A regression check
that counted these as wins could tell you a worse agent is fine.

## Agents, models and languages

`--agent` runs the coding agent you actually use, in a throwaway checkout,
and grades what it left behind:

| preset | verified | preset | verified |
|---|---|---|---|
| `claude-code` | yes | `gemini` | flags only |
| `opencode` | yes | `cursor` | flags only |
| `aider` | flags only | `copilot` | flags only |
| `codex` | flags only | `goose`, `amp` | flags only |

"Flags only" means the agent's documented non-interactive flags, not a run we
have watched succeed. `groundhog run --list-agents` prints the exact commands;
`--agent-cmd` takes anything else. If you verify a preset, please flip the flag.

An agent with a shell could simply read the fix: the full history is on disk
in the clone Groundhog was given and in its cache. So external agents run
under the OS sandbox (`sandbox-exec` on macOS, `bwrap` on Linux) with the
clone, the cache, the task file and earlier patches unreadable, and a test
checks that an agent which only tries to steal the answer scores 0. Each
attempt records whether it was sandboxed, and the report flags any that were
not. `groundhog doctor` shows whether this
machine has one, a run without one prints a warning before it starts, and
`--sandbox required` refuses to run at all; `--sandbox-deny PATH` covers
another copy of the repo. The public repository on the network is
out of its reach, so a public repo's recent commits remain findable online.

`--commit-tests` hands the agent the task's tests as a commit rather than as
uncommitted changes. The case study found an agent reverting uncommitted
tests as if they were stray edits; with them committed it never did
([#59](../../issues/59) proposes making this the default).

`--models` runs Groundhog's own small tool-calling loop against any model:

```
anthropic:claude-sonnet-5     ollama:qwen3:8b        openrouter:anthropic/claude-sonnet-5
openai:gpt-5.2                vllm:my-model          groq:llama-3.3-70b-versatile
deepseek:deepseek-chat        gemini:gemini-2.5-pro  together: xai: mistral: fireworks: cerebras:
```

Local hosts need no key and cost $0. Hosted ones read the usual environment
variable and say where to get one if it is missing. `groundhog run --list-providers`.

### Languages

| language | detected by | runner | status |
|---|---|---|---|
| Python | `pyproject.toml`, `setup.py`, `requirements*.txt` | pytest | validated on five real repos |
| Go | `go.mod` | `go test -json` | validated |
| JavaScript, TypeScript | `package.json` | vitest, jest | validated |
| Rust | `Cargo.toml` | `cargo test` | parser tested; [needs a real crate](../../issues/31) |

No Docker. Environments are inferred from the project's own dependency
declarations and cached. Anything else: `--test-cmd` and `--env-cmd`.

## How a task is built

Take a commit that touched both source and tests. Check out its parent. Copy
in the new tests but none of the new source. Run the tests twice:

```
parent + new tests             must FAIL    (the task is solvable)
parent + new tests + real fix  must PASS    (the task is fair)
```

A candidate becomes a task only if both hold. That single check separates a
bug fix from a refactor, and no amount of diff analysis can. Optionally the
fixed state is run again to reject flaky tests (`--stability`). Tasks that
test the same change (a revert cycle, a re-apply) are collapsed, so a
confidence interval's independence assumption holds.

The agent then sees the repo at the parent commit and the failing test
output. Test files are read-only. Scoring is
FAIL_TO_PASS and PASS_TO_PASS, the same shape SWE-bench uses, and
`groundhog export` writes SWE-bench instances.

## One run is also a leaderboard

Race several agents or models once, with no "before", and `report` ranks
them. Here from the study's own results file:

```
MODEL                       SOLVED   RATE         95% CI      COST   PER SOLVE   MEDIAN
---------------------------------------------------------------------------------------
openai:qwen3:8b              17/38    45%      30% – 60%         -           -     412s
openai:qwen2.5-coder:14b      8/22    36%      20% – 57%         -           -     111s
openai:qwen2.5-coder:7b       3/38     8%       3% – 21%         -           -      45s
openai:llama3.1:latest        1/38     3%       0% – 13%         -           -     111s
openai:mistral:latest         0/38          not measured         —           —     168s

  The top two intervals overlap — this ordering is not a result. Separating them
  would need roughly 538 tasks (you have 38), or use `groundhog compare`
  for a paired test, which needs far fewer.
```

Every rate carries a Wilson interval, because on a task set this size a gap
of ten points usually is not one. The last row is a model whose output the
harness could not read; it gets a refusal, not a 0%. You also get a Markdown
copy for the pull request, a shields.io badge, a standalone HTML page, every
attempt's diff, and a per-task grid; `groundhog show <task>` opens any
attempt.

Public leaderboards are a weak substitute: across 122 SWE-bench submissions,
repos agree on how to *rank* models (Spearman 0.86), but the median model
swings 36 points between its best and worst repository. "75% on SWE-bench"
says little about your repo. [docs/prior-art.md](docs/prior-art.md) has the
analysis and where Groundhog sits next to SWE-smith, SWE-bench-Live and
SWE-Factory.

## Groundhog on Groundhog

The first real run through `bench` was on this repository's own history: the
last two weeks of commits, 25 candidates, 22 validated tasks, Claude Code on
the newest ten. One command, no configuration:

```bash
groundhog bench . --agent claude-code --limit 10 --since "2 weeks ago"
```

| Model | Solved | Pass rate | 95% CI | F2P-only | Collateral | Median |
|---|---:|---:|---|---:|---|---:|
| claude-code | 10/10 | 100% | 72% – 100% | 10/10 | 0 | 222s |
| ollama:qwen3:8b | 1/10 | 10% | 2% – 40% | 1/10 | 0 | 676s |

The tasks were whole features written from their tests: the `bench` command
itself (eleven minutes), `--jobs` parallelism, `doctor`, the Markdown and
badge outputs, Rust detection. Every patch is in
[`study/dogfood/patches/`](study/dogfood/patches/) beside the human's fix, and
the task set, results and report are alongside. Read the interval, not the
point: ten tasks cannot distinguish 100% from 75%. And this is the easiest
possible target, a small stdlib codebase whose tests specify each feature
completely, so treat it as a demonstration of the pipeline rather than a
capability claim. Each Claude Code patch was also re-applied at its parent
commit with the whole suite run over it, which is the check file-scope
PASS_TO_PASS cannot make: all ten clean. The second row is an 8B local
model on the same tasks through Groundhog's own tool loop, at $0 and about
eleven minutes per attempt on a loaded laptop; it solved the one task whose
test names every string the output must contain. Details in
[`study/dogfood/`](study/dogfood/).

## What the study found

[`study/REPORT.md`](study/REPORT.md) is a pre-registered study of 174
attempts by five local models on five repositories in three languages, on one
laptop, for $0. It reports two things that worked and two that did not:

- **FAIL_TO_PASS-only scoring overstates.** The table above. Every patch is
  saved.
- **Models differ in how they work, not just how often they succeed.** Tool
  call counts, error rates and time-to-first-edit separate models that all
  scored zero.
- **The validity gate failed, twice.** A cripple test (same model, one turn
  instead of fourteen) did not score worse, because the un-crippled score was
  8% and there was no headroom. Reported as a failure, not buried.
- **One model was unmeasurable.** mistral wrote plausible fixes as markdown
  fences instead of tool calls. Groundhog refuses to print a percentage for a
  run like that rather than publishing a clean-looking 0%.

Live page: [scalingthoughts.com/groundhog/study.html](https://scalingthoughts.com/groundhog/study.html).

## What it cannot tell you

A solve rate is the share of *this slice* an agent can close: single commits
that changed source and tests, 3 to 200 lines, at most five files, with tests
that fail without the fix. That excludes features, refactors, multi-commit
work and every bug fixed without a regression test. Read it as a comparable
index across agent configurations, not as a forecast of your backlog.

Public repos may be in training data; a private repo is the cleanest
evaluation distribution there is. Tests are a proxy for correctness, and an
agent can pass them in ways a reviewer would reject. Building an environment
runs the repository's own install and test suite on your machine, so do not
point this at code you do not trust. The full list, including the bugs that
bit us, is in [docs/limitations.md](docs/limitations.md).

## Contributing

About 5,300 lines of stdlib Python in stages that each do one thing.
[CONTRIBUTING.md](CONTRIBUTING.md) explains how they fit. Most wanted:
[verify an agent preset](../../issues/39),
[harder tasks](../../issues/62),
[validate a Rust crate](../../issues/31).

- [Good first issues](../../issues?q=is%3Aissue+is%3Aopen+label%3A%22good+first+issue%22)
- [Help wanted](../../issues?q=is%3Aissue+is%3Aopen+label%3A%22help+wanted%22)
- [What people use this for](docs/use-cases.md) · [Research use](docs/research.md) · [CI](docs/ci.md)

## Citing

GitHub's "Cite this repository" button reads [`CITATION.cff`](CITATION.cff).
Please also report `groundhog --version`, the repository, the commit range
mined, and the task-set hash that `validate` prints. Task sets differ between
repositories and time windows, so a number without them is not reproducible.

```bibtex
@software{sv_groundhog_2026,
  author  = {Shankaranarayanan Venkataraman, Abhinav},
  title   = {{Groundhog}: Replaying Git History as a Regression Test for Coding Agents},
  year    = {2026},
  version = {0.2.0},
  url     = {https://github.com/abhinavsv3/groundhog},
  license = {Apache-2.0}
}
```

## License

Apache-2.0. See [LICENSE](LICENSE) and [NOTICE](NOTICE).
