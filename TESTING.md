# Testing

This is the source of truth for how this repo evaluates local models served by
llama.cpp's `llama-server` router (Ollama until 2026-09-14): runner usage, safety
notes, current benchmark results, and historical testing decisions. `README.md`
only carries the operational summary and compact leaderboards.

## Goals

The suite answers five practical questions:

1. **Can the model follow content instructions?** Format discipline, SEO keyword
   control, length, and Markdown structure.
2. **Can the model write correct code?** Pass@1 on small self-contained Python
   tasks with hidden asserts.
3. **Can the model teach without leaking?** Explanation quality, code gate, and
   solution-leak checks for tutor use.
4. **Can the model drive an agent loop?** The right tool with the right
   arguments, no call where none fits, and an answer that uses the tool result
   it was handed instead of the model's own memory (`run-tools.py`, added
   2026-09-17). This is the suite behind the Cline pick, which until then rested
   on prompt-ingest speed alone.
5. **Does the prompt stack actually hold?** Whether the rules in `prompts/`,
   `memory/` is obeyed — identity, honesty about Casey's skill
   buckets, `Unverified:` marking, output shape.

Question 5 is the odd one out and the reason `run-persona.py` exists: suites 1-4
measure the *base model* through the stack, so a prompt edit that silently breaks
a rule passes all of them. The stack is what this repo actually builds, so it gets
its own regression suite.

Speed is tracked separately because a better model that is too slow is not a
usable local default.

## Safety

`run-code.py`, `run-learn.py`, and `run-tutor.py` execute model-generated Python.
Since 2026-07-27 that execution is confined by **bubblewrap** when `bwrap` is
available on the box:

| Confinement | Effect |
|---|---|
| `--ro-bind /usr` + mirrored `/lib`,`/bin` | read-only system; nothing writable outside the CWD |
| `--unshare-all` | no network, no host PID/IPC/UTS namespace |
| `--bind <tmpdir>` + `--chdir` | writable only in the throwaway working directory |
| no `$HOME` bind | `~/.ssh`, `.env` files, and the rest of your home are unreachable |
| `--new-session`, `--die-with-parent` | no terminal injection back into the runner; no survivors |

Each runner prints its active mode at startup, e.g.
`Sandbox: bwrap (read-only /usr, no network, no $HOME, writable CWD only)`.

**The fallback is not isolation.** If `bwrap` is missing, or present but unable to
set up its namespaces (unprivileged user namespaces disabled, say),
`run_program()` degrades to a bare subprocess with only a wall-clock timeout and a
fresh CWD — model code then runs with your user's full access. That case is
detected by probe, never assumed, and the startup banner says `Sandbox: NONE`.
Read the banner before running an untrusted model; do not infer isolation from the
presence of this section.

Verified behavior under the sandbox: normal programs pass, `$HOME` reads raise
`FileNotFoundError`, outbound sockets raise `Network is unreachable`, CWD writes
succeed, and the `wrong-answer` / `syntax` / `timeout` failure classifications are
all preserved.

## Runner Matrix

All runners write to `eval/runs/<UTC>/`.

| Runner | Measures | Executes model code? | Default attempts | Confidence |
|---|---|---:|---:|---|
| `run-speed.py` | Raw generation tok/s, prompt tok/s, load time, GPU/CPU split | no | 1 per prompt | High for this machine |
| `run-content.py` | Format/instruction compliance across content tasks (SEO copy, technical explanation, Markdown brief) | no | 5 | Medium-high for prompt discipline |
| `run-code.py` | Real pass@1 against hidden Python asserts | yes | 5 per task | Medium-high for the covered task shapes |
| `run-learn.py` | Code + explanation, leave-one-out judge panel (median of `--judge-repeats` calls per judge) | yes | 3 per task | Medium because explanation quality is judge-scored; the code gate half is deterministic |
| `run-tutor.py` | Leak-gated tutoring guidance, same judge panel | yes | 3 per task | Medium because teaching quality is judge-scored; the leak gate is deterministic execution and does not depend on judges |
| `run-json.py` | Schema-constrained JSON + long-context fact recall | no | 3 per task | Medium for structured-output reliability at the tested context sizes |
| `run-tools.py` | Tool calling: right tool, right arguments, no call when none fits, and what the model does with the result it gets back | no | 3 per task |
| `run-persona.py` | Prompt-stack compliance: identity, skill-bucket honesty, `Unverified:`, output shape | no | 5 per task | Medium-high for the rule shapes encoded; regex scorers miss novel phrasings |

Common flags:

```bash
--models NAME
--attempts TIMES
--timeout SECONDS
--seed N
--out-root PATH
```

`run-learn.py` and `run-tutor.py` additionally take `--judges` (override the
panel) and `--judge-repeats N` (score each response N times per judge and take the
median; default 3).

Every runner preflights the router, the model names, and each model's built
`prompt.txt` before it creates a run directory, so a down router or an unbuilt
model stops the run with a message instead of showing up as failed attempts
(all eight, pinned by `eval/test_preflight.py`). `run-json.py`
then also checks each model's served context. See the 2026-07-28 note in
**Historical Notes** for why preflight matters:
a restart during a run previously produced complete, exit-0 summaries reporting
that every model scored zero.

**Every runner, and the judge panel, stops mid-run when the model it is calling
crashes** (since 2026-09-22, pinned by `eval/test_failure_paths.py`). A failed
call goes to the gateway's `after_failure()`, which asks the router about that
model and polls for up to 15 seconds, because the router can report the error
before it records the crash. A model the router marks `failed` stops the run with
its exit code and writes no summary. A router that stops answering stops it the
same way. A timeout, or an error from a model that stays loaded, is recorded as
that attempt's failure and the run continues. Before this, only three runners
aborted, and only when the router itself went down: on 2026-09-17 `qwen` crashed
while grading, the router stayed up, and `run-tutor.py` recorded 53 failed judge
calls as unparseable and exited 0. `run-code.py` and `run-speed.py` also caught
every exception, so a bug in their own scoring would have been recorded as the
model failing. They now catch only transport errors.

A server stopped mid-request surfaces as a connection error in every runner,
since `generate()` re-raises dropped connections as `URLError`. Before
2026-09-14 that case crashed the persona, content, and JSON runners with a
traceback instead of counting toward the abort.

### Reproducibility (`--seed`)

Every runner accepts `--seed N`. Without it the server samples freshly each call
and a run cannot be replayed. With it the run is meant to be repeatable, subject
to the warning below.

The seed is offset per attempt (`seed + attempt`), not reused verbatim. That
matters: a single fixed seed makes every attempt of a task byte-identical, so an
"N-attempt pass rate" would be one sample reported as N and the Wilson intervals
would be fiction. Offsetting keeps the run replayable while preserving the
between-attempt variance the suites exist to measure. Judge calls in
`run-learn.py` / `run-tutor.py` are seeded too — grading is part of the result.

> **⚠ Under Ollama, `--seed` did not give run-to-run reproducibility on this box.**
> Measured 2026-07-27 (see the llama.cpp re-measurement after this box):
>
> - **Within one warm process:** reliable. Six consecutive same-seed calls at
>   `temperature 1.6` produced byte-identical output, stable even when interleaved
>   with a different seed.
> - **Across separate runs:** not reliable. Re-running one persona task with the
>   identical `--seed 1000` produced **different text on 4 of 5 attempts**, and the
>   clean rate moved 5/5 → 4/5 purely from that variance.
>
> So the flag pins sampling inside a session but does not survive a process
> restart and model reload. `OLLAMA_KV_CACHE_TYPE=q4_0`, flash attention, and MoE
> routing under partial CPU offload are the plausible causes — offload decisions
> can differ between loads, which changes the numerics before sampling ever
> happens. Until that is run down, treat a seeded run as documented rather than
> replayable, and **do not attribute a small score change between runs to a code
> or prompt edit** without re-running both sides.
>
> Practical consequence: when comparing scorer or prompt changes, re-score the
> saved responses under `eval/runs/<UTC>/<suite>/<model>/` rather than re-running
> the models. That isolates the change from model variance.

The measurement above was taken under Ollama. The llama.cpp setup removes two of
the suspected causes: the GPU/CPU split is pinned per model (`fit = off`) instead
of chosen at each load, and the gateway sends `cache_prompt: false` on every call
because the llama-server docs state cached prefixes make logits not bit-identical.

**Re-measured on llama.cpp, 2026-09-15: seeds now mostly survive a restart.** One
open-ended prompt at temperature 1.5, seeds 1000 to 1004, through the gateway, in
three separate router sessions. Different seeds always produced 5 different
texts, so the check can tell seeds apart. Same-seed results:

| Model | Within one session | Across router restarts |
|---|---|---|
| `lite` | 5/5 | 5/5 (2 sessions) |
| `gemma` | 5/5 | 15/15 matches (3 sessions) |
| `qwen` | 5/5 | 14/15 matches (3 sessions) |

The one `qwen` miss was seed 1004 in the second session, which diverged at the
very first token and then matched again in the third session. The first token is
sampled before any MTP drafting, so the flip came from prompt processing, most
likely a near-tie at that high temperature tipped by small numeric differences
between loads. The cause was not isolated. A seeded persona run at the benchmark
temperature of 0.2 was also 5/5 identical within one session. Seeded runs are now
close to replayable, but the practical advice above still holds: compare scorer
or prompt changes by re-scoring saved responses.

`--out-root PATH` writes results outside the repo. This previously crashed every
runner (`Path.relative_to` raises rather than degrading when the target is outside
the repo); runners now fall back to printing the absolute path.

Runner-specific flags:

| Runner | Extra flags |
|---|---|
| `run-speed.py` | `--num-predict N`, `--thinking auto|on|off`, `--opt KEY=VAL` (per-request options only: `temperature`, `top_p`, `top_k`, `min_p`, `repeat_penalty`, `presence_penalty`, `seed`. Load-time settings such as `num_ctx` abort the run.) |
| `run-code.py` | `--tasks ...`, `--exec-timeout SECONDS`, `--thinking auto|on|off` |
| `run-content.py` | `--tasks ...`, `--prompt-file PATH` (ad-hoc SEO prompt), `--keyword TEXT`, `--thinking auto|on|off` |
| `run-learn.py` | `--tasks ...`, `--judges ...`, `--judge-rubric default|strict`, `--judge-repeats N` (default 3), `--exec-timeout SECONDS`, `--thinking auto|on|off` |
| `run-tutor.py` | `--tasks ...`, `--judges ...`, `--judge-rubric default|strict`, `--judge-repeats N` (default 3), `--exec-timeout SECONDS`, `--thinking auto|on|off` |
| `run-json.py` | `--tasks ...`, `--num-ctx N` (minimum served context, checked before the run, default 65536), `--context-pressure normal|medium|high`, `--position default|early|middle|late|all`, `--thinking auto|on|off` |
| `run-persona.py` | `--tasks ...`, `--system-mode stacked|baseline`, `--thinking auto|on|off` (defaults off) |
| `run-tools.py` | `--tasks ...`, `--thinking auto|on|off` (defaults off) |

Thinking mode can be forced with `--thinking on`, disabled with `--thinking off`,
or selected per model by appending `:think` to the model spec. Do not use
thinking mode for content runs unless explicitly testing it.

`run-json.py --context-pressure` scales document length to probe true
long-context degradation: `normal` (default) is the standard ~6-7k-token
prompts, `medium` lands ~15-19k, and `high` ~39-52k. `high` was resized on
2026-10-09 from a 6.0 to a 12.0 multiplier: at 6.0 it measured 21016 to 27067
prompt tokens, under half the 64k context served from 2026-09-16 to 2026-10-30,
and at 12.0 it measured 39196 to 51307 on `lite` with no context error and a 100%
score. At the 131072 context served since 2026-10-30, `high` fills about 40% of
the window. `high`
results from before 2026-10-09 used half-length documents and are not comparable
with later ones. A prompt that does overflow gets an HTTP 400 from
llama-server and is recorded as a failed attempt, never silently truncated. `--position early|middle|late|all`
moves the buried needle to measure position bias. Both are manual sweeps, not
part of the default comparison. `--judge-rubric strict` on the learn/tutor
runners pushes judges to reserve top marks when default grading saturates. On
the learn suite it did not: measured 2026-10-31, answers whose code passed still
scored 9.8 to 10.0 (*Strict-rubric learn run* under Historical Notes).

### Offline unit tests

The live suites above need the router and real models. The plumbing they depend
on has stdlib `unittest` tests that need neither, with responses captured from
the router on 2026-09-14 and `urlopen`, the clock, and `nvidia-smi` patched:

```bash
python3 -m unittest discover -s eval -p 'test_*.py'
```

| File | Covers |
|---|---|
| `eval/test_gateway.py` | `generate()` request shape (system prompt from `prompt.txt`, thinking flag, JSON schema shape, option mapping, `num_ctx` rejected), metadata normalization, dropped-connection handling, preflight and dead-server abort, `load_model()` cold-load timing, `served_ctx()` |
| `eval/test_speed.py` | `run-speed.py` offload label, GGUF size, per-process VRAM parsing |
| `eval/test_json.py` | `run-json.py` served-context check, no `num_ctx` in requests |
| `eval/test_preflight.py` | every runner preflights before creating a run dir (and `run-json.py` before reading served context), and imports the gateway's `preflight` |
| `eval/test_llm.py` | `scripts/llm` `status`, `load`, `unload`, `chat`, and `logs`, against router shapes captured 2026-09-15, with `urlopen`, the clock, `nvidia-smi`, stdin, and `execvp` patched |
| `eval/test_add_model.py` | `./add-model` run as a subprocess against synthetic GGUF headers and a fake `llama-server`: the menu, name validation, MTP detection from the header, the generated builder, the fit probe, dense and MoE offload pinning, fit-line provenance, HuggingFace lookup, and the checksum handoff |

Every test was seen failing before its change landed. Where a test could only
fail at first because its function did not exist yet, or because it guards
behavior the change kept, the code was also broken on purpose to confirm the test
catches a wrong implementation (24 such checks on 2026-09-14, all caught). Run
them after any change to `eval/_gateway.py` or the runner helpers.

## Interpreting Results

These tests are for personal model selection on this workstation, not broad
public claims about model quality. Treat failures as strong signal: a model that
misses schema, leaks a full tutoring solution, or fails hidden asserts is risky
for that use. Treat small wins as weak signal until repeated: a one-attempt or
one-task edge can be noise. When quality is tied or close enough to be unclear,
break ties by speed, load behavior, and GPU residency.

Every summary now reports its own uncertainty: a 95% Wilson confidence interval
on the headline rate, a small-sample flag below 10 attempts, the weakest task
per model, and a close-result warning when the winner's margin is within the tie
threshold (5 points for rates, 0.5/10 for judge scores). Learn/tutor summaries
add a judge-reliability section (parse rate, inter-judge disagreement, and a
warning when a response was scored by fewer than two judges). With the current
three-model lineup, leave-one-out leaves two judges per response, so
disagreement is a real number rather than the `n/a` a two-model lineup could
only ever print. That is what `lite` was added for. Tutor ranking breaks
teach-score ties on leak rate: the model that leaks less wins.

**Measured noise floor for `run-persona.py` at 3 attempts: up to 3 of 21 on a
single model.** The suite was run three times on 2026-09-15 and 2026-09-16 over
byte-identical inputs. `prompts/` and `memory/` had no commits
across that window, and the one change to the builders in between was proved to
leave every `prompt.txt` and every preset byte for byte identical.

| Model | Run 1 | Run 2 | Run 3 | Spread |
|---|---:|---:|---:|---:|
| `gemma` | 12/21 | 12/21 | 11/21 | 1 |
| `qwen` | 20/21 | 19/21 | 19/21 | 1 |
| `lite` | 12/21 | 10/21 | 13/21 | **3** |

Nothing about the models or the stack changed, so the whole spread is sampling
variation. `lite` alone moves 10/21 to 13/21, which is 14 points of clean rate,
wider than the 5-point tie threshold the summaries use. At 3 attempts a per-task
score is 0/3 or 3/3 on a coin flip: in run 3 `gemma` scored 0/3 on
`model_origin`, a task the 2026-07-28 stacked measurement had at 7/9 across the
lineup.

Do not read a small move at 3 attempts as a result, and do not rank `gemma`
against `lite` at this sample size at all. Three attempts is a gate, not a
measurement. Raise `--attempts` before believing a difference, and use
`./eval/run-profile.py` for anything that will be published.

Confidence by signal:

| Signal | Confidence | How to use it |
|---|---|---|
| Speed, load time, GPU/CPU split | High | Directly measured on this machine. |
| Code pass/fail | Medium-high | Real execution against hidden asserts, scoped to these tasks. |
| JSON schema + fact checks | Medium | Good structured-output smoke test, but still a small task set. |
| Content compliance | Medium-high | Useful for the project prompts, not a general writing benchmark. |
| Prompt-stack compliance | Medium-high | Deterministic and repeatable, but each scorer only catches the violation shapes written into it. Read a clean score as evidence, not proof; read a failure as reliable. |
| Learning explanation score | Medium | Leave-one-out judging reduces self-bias, but judges are still models. |
| Tutor score | Medium | Leak failures are strong. Teaching scores are judge-sensitive. |


## Benchmark Profiles

`./eval/run-profile.py` is the standard way to run comparisons; it wraps the
individual runners so routine testing doesn't drift across hand-typed flags.

```bash
./eval/run-profile.py smoke --models gemma qwen lite      # after a model rebuild
./eval/run-profile.py standard --models gemma qwen lite   # routine full comparison
./eval/run-profile.py deep --models gemma qwen lite       # pre-decision confidence run
./eval/run-profile.py standard --models gemma qwen lite --dry-run  # show commands
./eval/run-profile.py smoke --models lite --out-root /tmp/check    # results outside eval/runs/
```

| Profile | When to run | Runtime | What it does |
|---|---|---|---|
| `smoke` | After every `build-*` rebuild or runner change | ~5-10 min | Speed (capped output) + 2 coding tasks + SEO content + 2 persona tasks + 1 JSON task, 2 attempts each |
| `standard` | When picking models or after prompt-stack changes | ~1 hour | All eight suites; code/content/persona trimmed to 3 attempts so the expanded task set stays in budget |
| `deep` | Before trusting a close call or promoting a new model | several hours | Full 5-attempt sweeps, both persona system modes, plus medium and high context-pressure JSON runs |

Runtime note: the original "under 1 hour" `standard` budget assumed two models at
54/46.7 tok/s running fully or mostly on GPU. Two things have since inflated it.
Two of the three current models spill to CPU, and — the larger factor — the judged
suites scale with the **square** of the model count: `run-learn.py` and
`run-tutor.py` generate `models x tasks x attempts` responses and then grade each
with `models-1` judges x `--judge-repeats` calls. Going 2 -> 3 models roughly
triples the judging work. A 3-model `standard` took 97 minutes on 2026-09-15.
On 2026-09-17 it took 63, because `load-mode = none` and `ubatch-size = 1024`
roughly doubled prompt ingest on the two CPU-spilling models. Drop
`--judge-repeats` to 1 to trade the median back for speed.

The wrapper prints every `summary.md` it produced at the end. `--out-root` is
passed to every runner in the profile (since 2026-09-25, pinned by
`eval/test_profile.py`). Use it for any run that only checks that code or a new
llama.cpp build works, because `promote.py` publishes the newest run in
`eval/runs/` as the leaderboard. Individual runners
remain usable directly for targeted sweeps (single task, context pressure,
needle position, strict rubric).

Add tasks in:

| Task type | File |
|---|---|
| Coding correctness | `eval/coding_tasks.py` |
| Content compliance | `eval/content_tasks.py` |
| Code + learning explanation | `eval/learning_tasks.py` |
| Leak-gated tutoring | `eval/tutor_tasks.py` |
| Schema/long-context extraction | `eval/json_tasks.py` |
| Prompt-stack rule | `eval/persona_tasks.py` |

### Measuring what the prompt stack contributes

`run-persona.py --system-mode baseline` reruns the same tasks with a generic
`"You are a helpful assistant."` in place of the built stack. The delta against a
`stacked` run is the stack's actual contribution per rule:

- **Both high** — the base model complies unprompted; that rule is spending prompt
  tokens every turn for nothing and is a candidate for deletion.
- **Stacked high, baseline low** — the rule is load-bearing. Keep it.
- **Both low** — the rule is written but not working. Rewrite it or drop it.

That distinction matters here because `README.md` advises removing bad rules
before adding more instructions, and this is the only way to tell which rules are
bad. `deep` runs both passes; `standard` runs stacked only.

**Read baseline scores carefully — a clean baseline is not always compliance.**
Two of the tasks depend on facts that only exist in `memory/user.md`, so stripping
the stack removes the knowledge, not just the rule:

- `unknown_fact` asks for a figure that isn't on file. Stacked, a clean pass means
  the model consulted the profile and correctly said so. Baseline, the model has
  never heard of Casey, so it also says it doesn't know — and scores clean for
  entirely the wrong reason. **A high baseline here is an artifact, not evidence
  the rule is redundant.**
- `familiar_skill` is partly protected: a generic "I have no information about
  this person" does not match the required Familiar-framing language, so it still
  registers as a failure.

The tasks that give a genuinely clean read on the stack's contribution are the
ones whose rules are about *behavior* rather than *facts*: `identity`,
`model_origin`, `unverified`, `fields_echo`, and `bash_block`. Weight those when
deciding what to cut.

**Measured 2026-07-28** (moved from README on 2026-11-09). What each rule actually buys: `run-persona.py` stacked vs
`--system-mode baseline`, 3 attempts × 3 models, so each rule scores out of 9.
Nine attempts is a small sample, and the same suite's measured run-to-run spread
(see *Interpreting Results* in [`TESTING.md`](TESTING.md)) covers several of the
smaller gaps below. Treat a large delta as signal and a one or two point delta
as unresolved.

| Rule               | Stacked | Baseline |         Buys | Read                                                                                                                                                                                   |
| ------------------ | ------: | -------: | -----------: | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `identity`       |     9/9 |      0/9 | **+9** | Load-bearing. Without it every base volunteers a developer and training origin.                                                                                                        |
| `model_origin`   |     7/9 |      0/9 | **+7** | Load-bearing, same reason.                                                                                                                                                             |
| `familiar_skill` |     5/9 |      1/9 | **+4** | Load-bearing but leaky — no model holds it 3/3. The honesty rule most worth rewriting.                                                                                                |
| `unknown_fact`   |     6/9 |      3/9 | **+3** | Load-bearing on `qwen`/`lite`. `gemma`'s clean baseline is an artifact: with no profile it has never heard of Casey, so it declines for the wrong reason.                        |
| `unverified`     |     3/9 |      0/9 | **+3** | Works on `qwen` only; 0/3 on `gemma` and `lite` **stacked as well as unstacked**. See the negative result in [`TESTING.md`](TESTING.md) — rewriting it did not fix this. |
| `fields_echo`    |     3/9 |      0/9 | **+3** | Same shape: `qwen` 3/3, the other two 0/3 stacked.                                                                                                                                   |
| `bash_block`     |     9/9 |      9/9 |  **0** | Free. **Rule deleted 2026-07-28** — all three bases already fence commands in `bash` with no `$` prefix unprompted.                                                         |

Only `bash_block` was cut: it is the one rule confirmed at zero contribution
twice, on two different lineups. The deletion was verified with `make check` —
`bash_block` still scores 3/3 on all three models with the rule gone.

The honest caveat on the rest: `unverified` and `fields_echo` are not earning
their tokens on two of three models, but they are not *free* either — deleting
them would cost `qwen` a rule it does obey. They are candidates for a rewrite,
not a cut, and a rewrite has to be measured against a task that does not share
its subject with any example in the stack (that mistake is documented in
[`TESTING.md`](TESTING.md)).

## Current Benchmark Snapshot

### Build 12155 pass (2026-11-09)

One `standard` pass of the four-model lineup on llama.cpp build 12155
(`e30ad80fd`), on the live service at port 8080, same config as the 12009 passes
below plus `freedom` at 34 MoE layers on CPU. The profile ran 111.1 minutes from
15:24 UTC. Its coding suite aborted on a `gemma` Xid 31 (*GPU faults*, row 5)
and was rerun alone at 18:46 UTC with no fault. The README leaderboard is
promoted from these runs. This is the first benchmark of 12155 and of `freedom`.

| Suite | `lite` | `qwen` | `freedom` | `gemma` |
|---|---|---|---|---|
| Speed, gen tok/s | 139.7 | 58.3 | 57.1 | 47.1 |
| Speed, prompt tok/s | 3415 | 1039 | 1194 | 1541 |
| Coding | 20/27 | 26/27 | 24/27 | 25/27 |
| Content | 7/9 | 7/9 | 8/9 | 9/9 |
| Learning, teach /10 and code | 8.2, 10/12 | 9.9, 12/12 | 9.9, 12/12 | 9.8, 12/12 |
| Tutor, teach /10 and leaks | 5.8, 6/15 | 5.6, 6/15 | 3.9, 8/15 | 8.4, 2/15 |
| JSON, score and avg s | 100%, 2.3 | 100%, 7.1 | 100%, 6.7 | 100%, 5.5 |
| Prompt stack | 14/21 | 18/21 | 16/21 | 14/21 |
| Tool calling | 33/39 | 36/39 | 39/39 | 30/39 |

Runs: `eval/runs/20261109T152408Z` (speed), `20261109T184614Z` (code),
`20261109T152833Z` (content), `20261109T155652Z` (learn), `20261109T162935Z`
(tutor), `20261109T153907Z` (json), `20261109T153312Z` (persona),
`20261109T154642Z` (tools).

What it shows:

- **Learn and tutor are not comparable with the three-model tables.** With four
  models each response gets three judges instead of two.
- **`freedom` matches `qwen` on most suites** and has the best tool-calling
  score (39/39). It leaked the most on tutoring (8/15).
- **`qwen` dropped to 36/39 on tool calling** after 39/39 in both 12009 passes.
  `lite` dropped to 20/27 on coding after 25/27. One pass cannot separate the
  build change from run-to-run spread (`lite` has moved 3 points between
  identical passes before).
- **Speed held within the known spread.** `gemma` read 47.1 tok/s against 43.0
  and 46.1, and `lite` 139.7 against 132.5 and 134.7.

### Build 12009 passes (2026-10-30 and 2026-10-31)


Two full `standard` passes on llama.cpp build 12009 (`e216f4ec8`) at one config:
2026-10-30 (started 10:23 UTC, about 70 minutes) and a repeat on 2026-10-31
(started 09:31 UTC, 72.3 minutes). Three models, 3 attempts per task, the shared
sampler baseline in `server.ini` `[*]`, one model loaded at a time, pinned
offload (gemma 24 and qwen 37 MoE layers on CPU, lite all GPU), 131072 context,
`load-mode = none`, `ubatch-size = 1024` (lite 512), prompt caching off, on a
scratch router on port 8081 over the repo preset. The README leaderboard was
promoted from the 2026-10-31 repeat until 2026-11-09. Each table shows both passes, so the gap
between the two columns is run-to-run spread, not a config effect. Rows are
ranked by the repeat.

The 2026-10-30 prompt-stack suite did not finish. At 10:35:41 UTC, 19 seconds
into it, `gemma`'s model process hit NVIDIA Xid 31 during a decode and aborted
(see *GPU faults* under *Hardware*). It was rerun alone at 17:23 UTC with no
fault, and that rerun is its 2026-10-30 column. Casey reports a Brave video and
VS Code were using the GPU during that pass. Its profile and router logs were
lost to a reboot, so only its per-task counts survive. The 2026-10-31 repeat had
no GPU fault, no core dump, and no error-level line in the router log.

What the repeat shows:

- Unchanged task by task: `qwen` and `gemma` on tool calling (all 13 tasks),
  all three on JSON, `gemma` on coding, content, and the prompt stack, and
  `qwen` on the prompt stack.
- `lite` moved most. Coding rose from 22 to 25 of 27, so the 2026-10-30 drop was
  noise. On tool calling, three of its tasks flipped between 0/3 and 3/3 with
  nothing changed.
- Tutor leaks went 1 then 0 for `gemma`, 7 then 5 for `qwen`, and 7 both times
  for `lite`. Against 5 each for `qwen` and `lite` on 2026-10-20, the intervals
  overlap, so a rise is not shown.

What changed since the 2026-10-20 pass: the llama.cpp build (11768 to 12009),
context (65536 to 131072), and the pinned splits (gemma 22 to 24, qwen 35 to
37). A difference against that pass cannot be assigned to any one of them.
Samples are small (n = 9 to 27 per model), and the repeat confirms that a few
points either way is noise.

#### Speed (`run-speed.py`)

Runs: `eval/runs/20261030T102303Z/speed/summary.md` and
`eval/runs/20261031T093146Z/speed/summary.md`. Load and VRAM are from the repeat.

| Rank | Model | Gen tok/s 10-30 | Gen tok/s 10-31 | Prompt tok/s 10-30 | Prompt tok/s 10-31 | Load | GGUF | VRAM | GPU/CPU split |
|---|---|---:|---:|---:|---:|---:|---:|---:|---|
| 1 | `lite` | 134.7 | 132.5 | 3391 | 3354 | 4.4s | 5.5 GiB | 7.9 GiB | all GPU, MTP |
| 2 | `qwen` | 60.4 | 61.5 | 1008 | 1015 | 14.0s | 20.2 GiB | 5.9 GiB | 37 MoE layers on CPU, MTP |
| 3 | `gemma` | 46.1 | 43.0 | 1517 | 1462 | 8.0s | 13.4 GiB | 6.0 GiB | 24 MoE layers on CPU |

Finding: `lite` and `qwen` moved by 2.2 and 1.1 tok/s between the passes.
`gemma` read 3.1 tok/s (7%) lower, while its rate in the coding suite held at 44
then 45 tok/s, so the speed suite's two short prompts carry about ±3 tok/s of
spread on their own. Prompt ingest moved 4% or less. Against 2026-10-20 (47.4
tok/s), `gemma` read 1.3 and 4.4 tok/s lower with its two extra CPU layers, and
`lite`'s VRAM (7.9 to 8.1 GiB against 6.7) is the larger KV cache at 131072.

#### Coding (`run-code.py`)

Runs: `eval/runs/20261030T102358Z/code/summary.md` and
`eval/runs/20261031T093242Z/code/summary.md`. Avg s and tok/s are from the repeat.

| Rank | Model | Passed 10-30 | Passed 10-31 | Avg s | Tok/s |
|---|---|---:|---:|---:|---:|
| 1 | `gemma` | 26/27 | 26/27 | 7.4 | 45 |
| 2 | `lite` | 22/27 | 25/27 | 2.0 | 164 |
| 3 | `qwen` | 24/27 | 25/27 | 6.1 | 72 |

Finding: `gemma` missed one `calc` attempt in both passes, inside its 25 to 27
range. `lite` went from 22 to 25: `calc` and `lru_cache` passed 3/3 this time,
and `decode_string` stayed at 1 of 3. That is back near its 26 of 2026-10-20, so
the 2026-10-30 drop was not a regression. `qwen` passed `decode_string` once (0
of 3 on 2026-10-30), with a `TypeError` and a wrong answer on the other two.
`decode_string` is now the hardest task for both `qwen` and `lite`.

#### Content (`run-content.py`)

Runs: `eval/runs/20261030T103148Z/content/summary.md` and
`eval/runs/20261031T093944Z/content/summary.md`. Avg s, tok/s, and words are from
the repeat.

| Rank | Model | Clean 10-30 | Clean 10-31 | Avg s | Tok/s | Avg words |
|---|---|---:|---:|---:|---:|---:|
| 1 | `gemma` | 9/9 | 9/9 | 8.7 | 45 | 211 |
| 2 | `lite` | 8/9 | 8/9 | 3.6 | 112 | 194 |
| 3 | `qwen` | 8/9 | 7/9 | 10.8 | 49 | 245 |

Finding: `gemma` is 9/9, as in every pass since 2026-07-28. Every `qwen` miss is
`tech_explain` running long (216 words on 2026-10-30, then 207 and 206, against a
120 to 200 range). `lite` missed one `seo_product` attempt in each pass, after
9/9 on 2026-10-20. In the repeat that miss used the keyword 6 times against an
allowed 2 to 5. None of the 18 `qwen` replies on build 12009 came back empty
(148 to 402 words), the shape watched for since 2026-10-20.

#### Learning (`run-learn.py`)

Runs: `eval/runs/20261030T104838Z/learn/summary.md` and
`eval/runs/20261031T095937Z/learn/summary.md`. Explanation is from the repeat.

| Rank | Model | Teach 10-30 | Teach 10-31 | Code pass 10-30 | Code pass 10-31 | Explanation /10 |
|---|---|---:|---:|---:|---:|---:|
| 1 | `qwen` | 9.8 | 9.9 | 12/12 | 12/12 | 9.9 |
| 2 | `gemma` | 9.8 | 9.8 | 12/12 | 12/12 | 9.8 |
| 3 | `lite` | 8.0 | 9.0 | 10/12 | 11/12 | 9.8 |

Finding: `qwen` and `gemma` tie again, and the runner flags the 0.1 gap as inside
its tie threshold. `lite` passed the code gate 11 of 12 (an `lru_cache` attempt
used an undefined `Node` class), and that miss is its whole gap. In the repeat,
judges parsed 216/216 calls, within-judge spread 0.07/10 (0.12 on 2026-10-30).
The rubric is still saturated, and a strict-rubric run the same day scored 9.8 to
10.0 too, so the tie is the rubric's ceiling rather than a measured equality.

#### Tutor (`run-tutor.py`, leak-gated)

Runs: `eval/runs/20261030T110633Z/tutor/summary.md` and
`eval/runs/20261031T101805Z/tutor/summary.md`. Explanation is from the repeat.

| Rank | Model | Teach 10-30 | Teach 10-31 | Leaks 10-30 | Leaks 10-31 | Explanation /10 |
|---|---|---:|---:|---:|---:|---:|
| 1 | `gemma` | 8.9 | 9.1 | 1/15 | **0/15** | 9.1 |
| 2 | `qwen` | 5.1 | 6.0 | 7/15 | 5/15 | 9.0 |
| 3 | `lite` | 4.9 | 4.9 | 7/15 | 7/15 | 8.9 |

Finding: `gemma` leaked 1 of 30 attempts across the two passes. `two_sum` leaked
on all 3 attempts for `qwen` and `lite` in the repeat, and for `lite` on
2026-10-30 too (`qwen` 2 of 3). The other leaks moved between tasks. Against 5
of 15 each on 2026-10-20, `qwen` (7, then 5) and `lite` (7, then 7) stay inside
overlapping intervals, so a rise is not shown. The leak gate is deterministic.
In the repeat, judges parsed 270/270 calls, within-judge spread 0.50/10 (0.33 on
2026-10-30).

#### JSON / long-context (`run-json.py`)

Runs: `eval/runs/20261030T103637Z/json/summary.md` and
`eval/runs/20261031T094725Z/json/summary.md`. Columns after the scores are from
the repeat.

| Rank | Model | Score 10-30 | Score 10-31 | Schema OK | Fact rate | Avg s | Tok/s |
|---|---|---:|---:|---:|---:|---:|---:|
| 1 | `lite` | 100% | 100% | 100% | 100% | 2.3 | 134 |
| 2 | `qwen` | 100% | 100% | 100% | 100% | 7.3 | 62 |
| 3 | `gemma` | 100% | 100% | 100% | 100% | 5.5 | 43 |

Finding: a three-way 100% in every pass since 2026-09-15. The tasks top out at
about 51k prompt tokens even at high pressure, and this profile runs them at
normal pressure (about 6k to 7k), so neither pass exercised the extra context.

#### Prompt stack (`run-persona.py`)

Runs: `eval/runs/20261030T172326Z/persona/summary.md` (the 17:23 UTC rerun,
stacked only) and `eval/runs/20261031T094312Z/persona/summary.md`.

| Rank | Model | Clean 10-30 | Clean 10-31 | Clean rate 10-31 |
|---|---|---:|---:|---:|
| 1 | `qwen` | 19/21 | 19/21 | 90% |
| 2 | `lite` | 10/21 | 12/21 | 57% |
| 3 | `gemma` | 12/21 | 12/21 | 57% |

Finding: the same picture as 2026-10-20. `qwen` and `gemma` repeated task for
task: `qwen` missed `unverified` twice, and `gemma` failed `model_origin`,
`unverified`, and `fields_echo` 0/3. `lite` still fails `unverified` and
`fields_echo` 0/3 and passed `familiar_skill` 1 of 3 both times. Its only move
was `model_origin`, 0 then 2 of 3.

#### Tool calling (`run-tools.py`)

Runs: `eval/runs/20261030T104153Z/tools/summary.md` and
`eval/runs/20261031T095244Z/tools/summary.md` (13 tasks × 3 attempts). Avg s and
failed tasks are from the repeat.

| Rank | Model | Passed 10-30 | Passed 10-31 | Avg s | Failed tasks (0/3 each) |
|---|---|---:|---:|---:|---|
| 1 | `qwen` | 39/39 | 39/39 | 5.5 | none |
| 2 | `lite` | 36/39 | 33/39 | 1.7 | `result_beats_prior` (no-call), `tool_error` (unwanted-call) |
| 3 | `gemma` | 30/39 | 30/39 | 3.4 | `enum_arg` (wrong-arg), `result_beats_prior` and `empty_results` (no-call) |

Finding: this is the same-config repeat the tool suite lacked. `qwen` and `gemma`
matched task for task, `qwen` including `grounded_answer`, the task it declined
on all three 2026-10-20 runs. `lite` flipped three tasks with nothing changed:
`empty_results` went 0/3 to 3/3, and `result_beats_prior` and `tool_error` went
3/3 to 0/3. Every task still scored 0/3 or 3/3 within a pass, so at temperature
0 `lite` is consistent inside one pass but not between passes. Its earlier swaps
of first place with `qwen` (`lite` 39 on 2026-10-20, then 36 and 33) fit
run-to-run variation and need not come from the build or config changes. Why
`lite` varies between passes is not traced.

## Current Picks

From the 2026-10-31 repeat above, on build 12009 at 131072 context, with the
2026-10-30 pass as a check. No pick changed between the two passes. Not yet
revised for the 2026-11-09 pass on 12155.

| Use | Pick | Basis |
|---|---|---|
| Fast local default | `lite` | 132.5 tok/s fully on GPU, 2.2× `qwen` (61.5) and 3.1× `gemma` (43.0). |
| Agentic tools (Cline) | `lite` | Prompt ingest 3354 tok/s vs `gemma` 1462 and `qwen` 1015. On tool calling `qwen` led both passes (39/39 vs `lite` 36 then 33), so pick `qwen` when accuracy matters more than ingest speed. |
| Tool calling / agent loops | `qwen` | 39/39 in both passes vs `lite` 33/39 and `gemma` 30/39. `lite`'s score moved by 3 between two identical passes. |
| Coding puzzles / small functions | `gemma` | 26/27 vs 25/27 for `qwen` and `lite`, and 25 to 27 in every pass since 2026-09-15. The runner calls this pass a tie, so the pick rests on that record. |
| Content / SEO / copy | `gemma` | 9/9 clean in every pass since 2026-07-28. |
| Leak-gated tutoring | `gemma` | 0/15 leaks vs 5/15 (`qwen`) and 7/15 (`lite`), and 1 in 30 across both passes. Deterministic, not judge-scored. |
| Learning explanations | `qwen` or `gemma` (tie) | 9.9 and 9.8 with 12/12 on the code gate, inside the runner's tie threshold. |
| Structured JSON / consumer-app smoke tests | `lite` | Three-way 100%, and `lite` averages 2.3s against 5.5s and 7.3s. |
| Prompt-stack fidelity | `qwen` | 90% clean in both passes vs 57% for `gemma` and `lite`. |

## Hardware

Benchmarks are for this local machine:

| Component | Value |
|---|---|
| GPU | RTX 3080, 10 GB VRAM |
| CPU | Ryzen 5900x |
| RAM | 32 GB DDR4-3600 |
| Ollama | 0.30-era testing for the 2026-07-28 snapshot, 0.33.3 at the switch |
| llama.cpp | build 12155 (`e30ad80fd`) installed 2026-11-05, passed `verify-build` (VRAM +0 MiB against 12009 for all three models, smoke passed, no GPU fault), not yet benchmarked. Build 12009 (`e216f4ec8`) installed 2026-10-30, passed `verify-build` (VRAM +4 to +12 MiB against 11768, smoke passed, no GPU fault), benchmarked the same day at 131072 context and again on 2026-10-31 (the *Current Benchmark Snapshot* above and the README leaderboard), and spike-tested on 2026-10-31 (under *Historical Notes*). Build 11768 (`1ba568c5a`) installed 2026-10-20 and benchmarked the same day (under *Historical Notes*). 11537 (from 2026-10-09) and 11188 (from 2026-09-25) were never benchmarked. The previous snapshot and the tuning sweep are from build 11022 (`f172be756`, 2026-09-17, under *Historical Notes*), and everything from 2026-09-14 to 2026-09-16 from build 10968 (`41abbfd59`). |
| Desktop VRAM | about 1 GB held by desktop apps at idle, so about 8.6 GB for model plus KV cache |

Models that fit 100% on GPU are fast. Dense spillover usually collapses
generation speed because DDR4 bandwidth becomes the bottleneck. MoE spillover is
less punishing because only a subset of parameters is active per token.

The **Base-model speed survey (2026-08-11)** isolates this: a dense 27B and a
larger 35B A3B MoE spill nearly identically (74% vs 76% CPU), and the dense one
runs 4.6× slower. Size on disk does not predict throughput once spilling starts —
active parameters per token does.

### GPU faults (NVIDIA Xid 31)

`llama-server` has crashed 22 times since 2026-07-28 with `CUDA error: an illegal
memory access`, logged by the kernel as `NVRM: Xid 31` (a GPU MMU fault). Every
fault the journal holds, attributed on 2026-09-23 from the log lines before it:

| When | Runtime | Model | GPU layers | MoE layers on CPU | Vision projector | Crash after load |
|---|---|---|---|---|---|---|
| 2026-07-28 12:05 | Ollama | `qwen3.5:9b` (dense 9B) | 32/34 | none | yes | 7 s |
| 2026-07-28 12:53 | Ollama | `qwen3.5:9b` | 34/34 | none | yes | 6 s |
| 2026-07-28 18:14 | Ollama | `lite` (same weights) | 34/34 | none | yes | 6 s |
| 2026-07-28 19:03 | Ollama | `lite` | 34/34 | none | yes | 45 s |
| 2026-08-06 08:09 | Ollama | `qwen3.5:9b` | 29/34 | none | yes | 10 s |
| 2026-08-10 00:34 | Ollama | `qwen3.5:9b` | 32/34 | none | yes | 51 s |
| 2026-08-10 00:34 | Ollama | `qwen3.5:9b` | 32/34 | none | yes | 7 s |
| 2026-08-12 10:09 | Ollama | `gemma4:26b-a4b-it-qat` (MoE) | 31/31 | 23 | yes | 14 s |
| 2026-08-12 10:09 | Ollama | `gemma4:26b-a4b-it-qat` | 31/31 | 23 | yes | 14 s |
| 2026-08-12 20:04 | Ollama | `gemma4:26b-a4b-it-qat` | 31/31 | 24 | yes | 67 s |
| 2026-08-23 06:18 | Ollama | `gemma4:12b-it-qat` (dense 12B) | 48/49 | none | yes | 45 s |
| 2026-08-24 07:50 | Ollama | `qwen3.5:9b` | 34/34 | none | yes | 23 s |
| 2026-08-29 12:19 | Ollama | `qwen3.5:9b` | 33/34 | none | yes | 17 s |
| 2026-08-29 12:19 | Ollama | `qwen3.5:9b` | 33/34 | none | yes | 4 s |
| 2026-09-17 04:11 | router, build 11022 | `qwen` (MoE) | all | 35 | no | 347 s |
| 2026-09-17 05:03 | router, build 11022 | `gemma` (MoE) | all | 22 | no | 34 s |
| 2026-09-17 18:31 | scratch router, build 11022, `q8_0` KV | `gemma` | all | 22 | no | unknown (1) |
| 2026-09-22 07:36 | service, build 11022 | `qwen` | all | 35 | no | 2478 s |
| 2026-09-26 14:10 | service, build 11188 | `qwen` | all | 35 | no | about 1900 s (2) |
| 2026-10-30 06:35 | scratch router, build 12009, 131072 context | `gemma` | all | 24 | no | about 19 s (3) |
| 2026-10-31 11:55 | scratch router, build 12009, 131072 context | `gemma` | all | 24 | no | 108 s (4) |
| 2026-11-09 10:28 | service, build 12155, 131072 context | `gemma` | all | 24 | no | 148 s (5) |

(1) This one ran on a scratch router whose log went to a file, not the journal.
The model comes from the KV cache note above, and the journal's nearest service
log line (which named `lite`) is not this process.

(2) Found on 2026-10-09 while re-checking the record. It happened the day after
11188 went live and had not been written down. Same signature as 2026-09-22:
`illegal memory access` in `ggml_backend_cuda_synchronize` during a decode, MMU
fault at `GPC4 GPCCLIENT_T1_9`, address `0x7f82_0b5de000`. Measured from the
start of the load (13:39:09), because the router log has no load-finished line
for that instance.

(3) During the 2026-10-30 `standard` pass, 19 seconds into the prompt-stack
suite, measured from the suite's start because the router log was lost to a
reboot. Same signature as (2): abort in `ggml_backend_cuda_synchronize` during a
decode, MMU fault at `GPC4 GPCCLIENT_T1_9`, `FAULT_PDE`, host-range address
`0x7f30_156dc000`.

(4) During a `run-learn.py --judge-rubric strict` run, while `gemma` was judging,
108 s after its load finished. Abort in `ggml_backend_cuda_synchronize` called
from `ggml_backend_sched_copy_input` (an input copy between the CPU and GPU
backends), MMU fault at `GPC4 GPCCLIENT_T1_9`, `FAULT_PDE`, `VIRT_READ`,
host-range address `0x7f6b_61586000`. No VRAM holder was running. GPU use read
938 MiB just before the run, and the process list four minutes before that held
only kwin, Brave's GPU process, and VS Code, so about 3 GB was free. The
`nvidia-smi` capture after the crash came 20 minutes late, after the next
measurement had run, and showed the same three desktop processes. The core dump
(9.6 GB) is in systemd-coredump.

(5) During the four-model `standard` pass, in the coding suite, 148 s after
`gemma`'s load finished (loaded 10:25:36, abort 10:28:04). Same signature as (4):
abort in `ggml_backend_cuda_synchronize` called from
`ggml_backend_sched_copy_input` during a decode, MMU fault at
`GPC4 GPCCLIENT_T1_9`, `FAULT_PDE`, `VIRT_READ`, host-range address
`0x7f92_cdb49000`. Driver 615.78.08, kernel 7.2.14. The router log shows only
the benchmark's own requests in suite order, so no other app was calling the
router. The `nvidia-smi` capture came about two hours late and showed kwin and
VS Code. The core dump (9.6 GB) is in systemd-coredump.

What the table settles:

- **The CPU-offloaded expert path is not required.** Five crashes had the dense
  9B entirely on the GPU (34/34 layers, no experts on CPU). The first hypothesis,
  that the fault follows the GPU reading expert weights from system RAM, is
  broken.
- **No one model, runtime, or build.** Dense 9B, dense 12B, and two MoE models,
  under Ollama's bundled llama.cpp and builds 10968, 11022 and 11188, across drivers
  610.43, 610.57 and 615.71, kernels 7.1.5 to 7.2.6, and CUDA 13.3 and 13.4.
- **The vision projector is not required.** Every Ollama crash had one loaded
  and none of the router crashes did.
- **Under Ollama, every crash came within 67 s of a model load**, mostly on the
  first request. The router crashes came later (19 s to 41 minutes). Ollama
  reloaded models far more often than the router does, so this may reflect how
  often each loaded rather than a load-time trigger. It is not established.
- **Something other than llama.cpp hits the same fault.** A game (`Sand.exe`)
  logged Xid 31 on 2026-08-24, and games have logged Xid 32 (2026-08-10) and
  Xid 109 context-switch timeouts (four times on 2026-08-31).
- **20 of the 21 fault addresses are in the host program-memory range**
  (`0x7f..`), including crashes with the model fully on the GPU. The game's
  fault and one router fault were at GPU-range addresses.
- **Not measured:** crash rate per model. The journal records crashes, not hours
  of use, so the table cannot say whether one model crashes more often per hour
  than another.

The card has no ECC, so it cannot report VRAM errors itself, and PCIe replays
since boot read 0.

**VRAM stress test, 2026-09-25: clean.** With the service stopped, a small CUDA
pattern tester (built with the installed `nvcc`, nothing new installed) wrote and
verified four patterns (address-in-address, its inverse, pseudo-random, and a
checkerboard) over 7,800 MiB for 20 minutes: 9,866 passes, 0 mismatches, no Xid.
The card ran at full memory clock (9,251 MHz), 1,965 MHz core, up to 306 W and
64 °C, which is harder than any crash's conditions. The tester was first shown to
catch an injected error, reporting exactly one mismatch per pattern pass at the
corrupted word. This makes bad VRAM an unlikely cause. It did not test the path
17 of the 18 faults point at: the GPU reading **host** memory (the `0x7f..`
addresses), which is where the driver's unified-memory handling and the open
kernel module come in.

**Host-memory stress test, 2026-09-25: clean, but short.** The driver reports
`Addressing Mode: HMM` (`uvm_disable_hmm = N`), so the GPU can read plain `malloc`
memory directly, which is the address range 17 of the 18 faults hit. A second
scratch tester, again shown first to catch injected errors, had the GPU write and
the CPU verify (and the reverse) three kinds of host memory for 20 minutes:
pinned zero-copy, unified memory migrating between CPU and GPU, and pageable
`malloc` memory through HMM, 512 MiB each. 1,377 passes, 0 mismatches, no Xid.
This does not clear the host-memory path. The crash is rare (at worst 3 in about
12 hours of heavy load on 2026-09-17), so 20 clean minutes is what the fault
would also produce by chance. A test that can clear or convict it has to run for
hours, and ideally on llama.cpp's own workload.

**Investigation paused, 2026-09-25.** Casey's working explanation is too many
processes running in the background at the time of the crashes. The tests above
did not cover that, so it is untested rather than ruled in. If the crash
returns, capture what else was using the GPU at the time
(`nvidia-smi --query-compute-apps=pid,process_name,used_memory --format=csv`, and
`journalctl -k -b | grep -i xid`) before restarting anything. The runners now
stop on a crashed model instead of recording it (see *Runner Matrix*), so a
recurrence cannot silently corrupt a benchmark run. The next step, if it comes
back, is an overnight soak on llama.cpp's own workload, then turning HMM off
(`uvm_disable_hmm=1`) and repeating.

**Closed, 2026-10-09.** Checked across every boot the journal still holds
(2026-09-20 onward, each boot read separately, because `journalctl -k` reads only
the current boot unless one is named): two faults, 2026-09-22 and 2026-09-26, both
in the table. None since, over 13 days and 41 model loads, though use in that
window was light (loads on five days). Build 11537, live from 2026-10-09,
includes upstream fixes for MMQ out-of-bounds reads (`fc9ce6b9d`) and an MMQ
memory fault when experts outnumber the batch (`dd266785c`), which touch the
kernels the MoE models use. Neither is shown to be this fault. Casey closed the
investigation on the quiet window. **Reopen trigger: any new Xid 31.** Capture the
`nvidia-smi` and `journalctl` lines above before restarting anything, and add the
row to the table.

**Triggered, 2026-10-30.** One fault, row (3) above, the first in 34 days. The
`nvidia-smi` capture was missed: the scratchpad holding the run logs was wiped by
a reboot before the crash was found, and only the kernel journal and the core
dump survived. Casey reports a Brave video and VS Code were using the GPU at the
time, which fits the 2026-09-25 working explanation (other GPU work running
alongside) and remains untested. The prompt-stack suite then reran in full on the
same build and preset with no fault. Whether to resume the investigation is
Casey's call.

**Triggered again, 2026-10-31.** Row (4), the day after (3), again `gemma` and
the same signature. This time nothing was known to be loading the GPU: no holder
was running and other GPU use was about 0.94 GB, so VRAM pressure is not needed
for the fault. Whether Brave was playing video is not recorded. Earlier the same
day the full `standard` pass (72 minutes) and 20 `gemma` and `qwen` spike-test
cycles under about 3.0 GB held ran clean. After a reboot, the same suite reran in
full at 13:22 with no fault (about 18 minutes, `gemma` included), with only
kwin and VS Code on the GPU at its start. Whether to resume the investigation is
Casey's call.

**Triggered again, 2026-11-09.** Row (5), the first fault on build 12155. The last
three faults are all `gemma` at 24 MoE layers on CPU and 131072 context. The runner stopped the coding suite as designed, and the other seven
suites of the pass finished clean, `gemma` included. Whether to resume the
investigation is Casey's call.

## Models Tested

| Model | Status | Notes |
|---|---|---|
| `gemma` (`gemma4-26b-a4b-it-qat.gguf`) | current | Rebuilt 2026-07-28. 26B A4B MoE, QAT Q4_0, 13.4 GiB file. Byte-identical to the Ollama `gemma4:26b-a4b-it-qat` blob (sha256 `4c856523…`). On llama.cpp since 2026-09-14, with 21 MoE layers on CPU since the headroom fix the same day. |
| `qwen` (`qwen3.6-35b-a3b-mtp-q4_K_M.gguf`) | current | Rebuilt 2026-07-28. 35B A3B MoE, 20.2 GiB file. Official release, and `build-qwen` was reverted to it from the uncensored tune below. Byte-identical to the Ollama blob (sha256 `d372de8e…`). On llama.cpp since 2026-09-14 with MTP on, and 34 MoE layers on CPU since the headroom fix the same day. |
| `lite` (`qwen3.5-9b-mtp-q4_K_M.gguf`) | current | Added 2026-07-28. Dense 9B, 5.5 GiB file, the only model that fits entirely in 10 GB. Exists as a no-spillover speed control and as the third judge, which is what makes inter-judge disagreement computable at all. Base replaced 2026-09-14 with unsloth `Qwen3.5-9B-MTP-GGUF` Q4_K_M (sha256 `e8dd9481…`) because the Ollama file does not load in llama.cpp. |
| `lite` (`qwen3.5:9b`, Ollama) | replaced 2026-09-14 | Source of every `lite` score before the switch. Fails to load in llama.cpp: `key qwen35.rope.dimension_sections has wrong array length; expected 4, got 3`. |
| `qwen` (`hf.co/HauhauCS/Qwen3.6-35B-A3B-Uncensored-HauhauCS-Aggressive:Q4_K_M`) | reverted 2026-07-28, never benchmarked | Targeted by `build-qwen` on 2026-07-27; the base was never pulled, so the builder's preflight failed and no run ever used it. Reverted rather than pulled: an uncensored tune works against `prompts/safety.md` by construction, so the shared stack would spend tokens every turn fighting the base's own tuning, and the persona suite would be measuring that fight instead of the stack. If it is ever wanted, it belongs on its own tag with its own stack, not swapped under the shared one. |
| `gemma4:12b-it-qat` | installed 2026-08-11, speed-only | Not in the lineup and not built into a tag. 7.3 GB, 98% on GPU, 59.7 tok/s — fastest base after `lite`. Quality untested. Distinct from the retired `gemma4:12b-it-q4_K_M` below: different quant (QAT). |
| `qwen3.6:27b-mtp-q4_K_M` | installed 2026-08-11, speed-only | Not in the lineup. Dense 27B, 18 GB, 8.6 tok/s under spillover — 4.6× slower than the larger 35B A3B MoE. Batch-only on this box. |
| `freedom` (`Qwen3.6-35B-A3B-Uncensored-HauhauCS-Aggressive-Q4_K_M.gguf`) | current, added 2026-11-08, not yet benchmarked | Uncensored tune of `qwen`'s base, the same tune as the reverted uncensored `qwen` row below, under its own name. For Friends-Hermes, a router contract name since 2026-11-09. No MTP heads, 34 MoE layers on CPU, spike-tested 2026-11-09 (*Spike test of freedom*). sha256 `bbef58c3…`. |
| `bigqwen` (`qwen3.8-27b-mtp-q4_K_M.gguf`) | added 2026-11-08, retired 2026-11-09 | Added with `./add-model` only to measure its speed. Dense 27B with MTP, 16.8 GB file. At 131072 context and the 2 GB margin only 12 of 66 layers fit on the GPU (6109 MiB used). On build 12155 the 2026-11-08 persona run gave 7 tok/s and 17.7 s per reply, 16/21 clean. MTP took it from the 4 tok/s of the 2026-10-09 load check to 7, which is still too slow for interactive use. |
| `gemma4:31b-it-qat` | speed-only | Not in the lineup. Dense 31B, 18 GB, 3.1 tok/s under 72% CPU spill. Slowest base measured here; effectively unusable interactively. |
| `gemma` (`gemma4:12b-it-q4_K_M`) | retired 2026-07-27 | Base no longer installed. Won/tied all six suites on 2026-06-14, but that run was sampler-confounded. |
| `granite` / `granite-custom` (`granite4.1:8b-Q5_K_M`) | dropped | Strong prior coding runs, but no longer leads the current lineup. |
| `qwen-custom` (`qwen3.5:9b`) | removed | Fast 9B-era thinking model; superseded by current Qwen3.6 MoE results. The base returned 2026-07-28 as `lite`. |
| `ministral-custom` (`ministral-3:8b`) | removed | Strong historical #2; removed after Gemma/Granite consolidation. |
| `llama-custom` (`llama3.1:8b`) | removed | Last or near-last in early content/coding/teaching runs. |
| `qwen-big` / `qwen-moe` experiments | promoted/retired variants | Established that MoE can survive spillover; current `qwen` is the promoted MTP MoE line. |
| `gemma-big` (`gemma3:27b`) | retired | Larger dense Gemma lost quality/speed tradeoffs on this hardware. |
| `gemma-custom` (`gemma4:e4b`) | removed | Superseded by gemma4 12B. |

## Historical Notes

The notes below are retained for decision history. Prefer the current snapshot
above when choosing a model today.

### Pinned split history (moved from README on 2026-11-09)

**The values leave about 2 GB of VRAM free, because 1 GB was not enough.** The
first pinned values (gemma 18, qwen 32) came from llama.cpp's default 1 GB fit
margin and crashed with CUDA out of memory once other GPU use passed somewhere
between 2.1 and 2.5 GB for `gemma` and between 2.5 and 3.0 GB for `qwen`. Desktop apps alone use 1.2 to 1.5 GB
on this box. The 2 GB-margin values at 32768 context (gemma 21, qwen 34) were
then checked with 5 load-and-request cycles each while 3.0 GB of the card was
held by other processes: all passed. The cost against the old values was about
13% generation speed for `gemma` and 9% for `qwen` (details in
[`TESTING.md`](TESTING.md) under *Offload headroom*). The values were then
re-derived at the same 2 GB margin each time the context grew: gemma 22 and qwen
35 at 65536 on 2026-09-16, and the current gemma 24 and qwen 37 at 131072 on
2026-10-30 (build 12009). 22 and 35 were replaced before they had the 3.0 GB
spike test. 24 and 37 passed it on 2026-10-31, 5 of 5 cycles each, with about
1.1 GB (gemma) and 1.2 GB (qwen) still free at peak. At 131072, gemma generates
about 5 to 8% slower than at 22, and qwen showed no measurable change. `lite`
stays all-GPU by Casey's choice, with only about 0.4 GB free at 131072 while the
desktop holds 1.4 GB, so a desktop VRAM spike can crash it. It runs with up to
about 1.7 GB of other GPU use, about 0.75 GB above the 0.94 GB the desktop held
when that was measured (2026-10-31). At 1.8 GB it loaded and then crashed on its
first request, which a client sees as an HTTP 500.

Re-probed on build 11022 on 2026-09-17 at about 1.1 GB of desktop use. `qwen`
reproduced its pinned value exactly (`42 layers (35 overflowing), 6168 MiB used`). `gemma`'s fitter now picks 21, not 22 (`31 layers (21 overflowing), 6403 MiB used`, the same in two runs). The pin stays at 22, which keeps more
VRAM free than the 2 GB margin asks for. Casey chose that while two
`llama-server` crashes (NVIDIA Xid 31) on 2026-09-17 are unexplained.

**`server.ini` reads weights into RAM and uses a 1024-token physical batch.**
Two settings in `server.ini` were added on 2026-09-17 because prompt ingest was
the slow spot for the two models that keep experts in system RAM.
`load-mode = none` stops the kernel from evicting those weights out of page cache,
and `ubatch-size = 1024` processes prompts in larger chunks. On build 11022,
prompt ingest went from 665 to 1565 tok/s for `gemma` and from 379 to 1030 for
`qwen`, with generation unchanged and model VRAM up only 0.1 to 0.2 GiB, so the
pinned splits still hold. Loads take 3 to 10 seconds longer. `lite` keeps
`ubatch-size = 512` in its own `LOAD`, because at 1024 it no longer fits
entirely on the GPU with the 2 GB margin. The full sweep is in
[`TESTING.md`](TESTING.md) under *Preset tuning on build 11022*.

### llama.cpp build history (moved from README on 2026-11-09)

The installed build is 12155 (commit `e30ad80fd`), swapped in on 2026-11-05
through `scripts/llama-update` after `scripts/verify-build` passed it: at every
pinned split it needed exactly the same VRAM as 12009 (gemma 6066, lite 8038,
qwen 5928 MiB), the smoke profile passed, and no GPU fault was logged. Build
12009 (commit `e216f4ec8`) ran from 2026-10-30 and is now the rollback target.
12155 has not been benchmarked yet. The
current leaderboard was measured on build 12009 on 2026-10-31, repeating the
2026-10-30 pass at the same config. The ones before it
came from build 11768 (2026-10-20) and build 11022 (commit `f172be756`,
2026-09-17), and results from
2026-09-14 to 2026-09-16 came from build 10968 (commit `41abbfd59`). llama.cpp is built from
source because the AUR `llama.cpp-cuda` package was reported stale. The recipe
used to pin `g++-15` as the CUDA host compiler. That is corrected as of
2026-09-15: this box has no `/usr/bin/g++-15`, so the block as written failed at
the configure step, and `llama-server --version` reports the installed binary was
in fact built with GNU 16.2.1. Nothing needs pinning. Verified by configuring in
a scratch directory: cmake reports `CUDA host compiler is GNU 16.2.1` against
CUDA 13.4.59.

The configure step uses `--fresh`, which discards `build/CMakeCache.txt`. On
2026-09-17 the update to 11022 failed at configure because the cache still held
`CMAKE_CUDA_HOST_COMPILER=/usr/bin/g++-15` from the old recipe. The new
CMakeLists asks nvcc for the host compiler version with errors suppressed, so
the only symptom was `CUDA host compiler is GNU` with no version, followed by
`ggml_get_flags Function invoked with incorrect arguments`.

### Other GGUFs on disk (load checks 2026-10-09, moved from README on 2026-11-09)

Casey deleted all four files, and `gemma4-12b-it-qat.mmproj.gguf`, on 2026-11-09,
leaving only the lineup in `~/models/gguf`. They have no byte-identical public
source, so this record is all that remains of them.

`~/models/gguf` held four models copied out of Ollama before it was removed,
checked against their blob digests: `granite4.2-8b-q4_K_M.gguf`, `ornith.gguf`,
`qwen3.8-27b-mtp-q4_K_M.gguf` and `gemma4-12b-it-qat.gguf`. Only
`gemma4-12b-it-qat` has its `.mmproj.gguf` vision projector on disk (checked
2026-09-17). Ollama's params, licenses, and ornith's built-in system prompt were
not kept: no `ollama-extras/` folder exists (checked 2026-10-09). None is in the
lineup. On 2026-10-09 each was loaded once on build 11537 (standalone, 8192
context, `--fit-target 2048`, text only) and answered a one-line request
correctly:

| File                            | Architecture       | Layers on GPU | Speed (short reply) |
| ------------------------------- | ------------------ | ------------: | ------------------: |
| `granite4.2-8b-q4_K_M.gguf`   | granite            |         41/41 |            97 tok/s |
| `ornith.gguf`                 | qwen35             |         33/33 |            94 tok/s |
| `gemma4-12b-it-qat.gguf`      | gemma4             |         42/49 |            30 tok/s |
| `qwen3.8-27b-mtp-q4_K_M.gguf` | qwen35 (dense 27B) |         24/66 |             4 tok/s |

These are load checks, not benchmarks. The speeds come from one short reply at a
small context, and the 27B ran without its MTP heads (no `--spec-type`). Adding
one to the lineup goes through `./add-model`. The 27B was added that way as
`bigqwen` on 2026-11-08 to measure it with MTP: 12 of 66 layers on the GPU at
131072 context, 7 tok/s. It was retired the next day (TESTING, *Models Tested*).

### MoE GPU cache at a refitted split (2026-11-11)

Follow-up to *MoE GPU cache* below, which ran 1024 MiB at today's pins and broke
the 2 GB margin. Here each model was first refitted with the cache counted
(`./add-model --refit` over a scratch preset with `moe-cache-mib = 1024`, at
`--fit-target 2048`, build 12155), then measured at that split against its
current pin without the cache. Method as below: a scratch server per config, the
`verify-build` footprint command, three identical 256-token answers, thinking
off. The pin ran twice, for the noise floor. About 1.4 GB of other GPU use.

The fitter counts the cache. It moved more experts to the CPU to keep the
margin:

| Model | Pin (no cache) | Refit with 1024 MiB | Free in fit line, without and with |
|---|---:|---:|---|
| `gemma` | 24 | 27 | 2074 and 2270 MiB |
| `qwen` | 37 | 39 | 2158 and 2132 MiB |
| `freedom` | 34 | 37 | 2102 and 2150 MiB |

| Model | Config | Generation (tok/s) | Process VRAM | Card peak |
|---|---|---|---:|---:|
| `gemma` | 24, no cache | 47.8, 49.0, 48.8 | 6116 | 7543 |
| | 24, no cache (repeat) | 45.8, 48.8, 48.7 | 6116 | 7543 |
| | 27, 1024 MiB | 45.9, 46.0, 45.8 | 5922 | 7351 |
| `freedom` | 34, no cache | 56.4, 57.4, 57.3 | 6168 | 7607 |
| | 34, no cache (repeat) | 56.6, 58.4, 58.6 | 6168 | 7607 |
| | 37, 1024 MiB | 54.0, 54.1, 54.1 | 5720 | 7161 |
| `qwen` | 37, no cache | 52.7, 58.8, 58.6 | 6012 | 7453 |
| | 37, no cache (repeat) | 53.5, 57.0, 58.2 | 6012 | 7453 |
| | 39, 1024 MiB | 33.3, 33.3, 33.4 | 6040 | 7483 |

Findings:

- With the margin kept, the cache is a loss for all three: `gemma` about 5%
  slower, `freedom` about 6%, `qwen` about 42%. The experts it moves to the CPU
  cost more than the cache wins back.
- The earlier gain does not hold. *MoE GPU cache* below measured `gemma` at
  42 tok/s without the cache and 48.5 with it, at the same pin. Today the pin
  alone gave 48.5. That session's baseline was low, so its +15% (and
  `freedom`'s +6%) was most likely run-to-run variation between sessions, not
  the cache.
- Decision on 2026-11-11: no model adopts `--moe-cache-mib`. The flag now
  reaches `add-model`'s probe and `verify-build` if a preset sets it.

### MoE GPU cache, `--moe-cache-mib` (2026-11-11)

Build 12155 adds `--moe-cache-mib N`, a GPU cache for the expert weights kept on
the CPU (default 0, off). Measured on the three MoE models at their pinned
splits, each with a scratch `llama-server` on a spare port (the `verify-build`
footprint command plus the flag), build 12155, about 1.3 GB of the card held by
the desktop. Each value is one load and three identical 256-token answers to the
same short prompt, with thinking off and prompt caching off. 0 ran twice, for
the noise floor. VRAM is the process's own (nvidia-smi) and the card's peak
during the run.

| Model | MiB | Generation (tok/s, 3 answers) | Process VRAM | Card peak |
|---|---:|---|---:|---:|
| `gemma` 24 | 0 | 41.9, 42.9, 43.3 | 6116 | 7445 |
| | 0 (repeat) | 40.9, 41.5, 42.1 | 6116 | 7431 |
| | 256 | 28.0, 28.0, 28.3 | 6386 | 7780 |
| | 512 | 28.5, 28.3, 28.2 | 6640 | 7982 |
| | 1024 | 49.0, 48.6, 47.9 | 7150 | 8724 |
| `qwen` 37, MTP | 0 | 50.9, 52.6, 53.1 | 6012 | 7433 |
| | 0 (repeat) | 51.2, 52.4, 53.1 | 6012 | 7422 |
| | 256 | 33.0, 33.0, 33.1 | 6268 | 7742 |
| | 512 | 32.9, 33.2, 33.4 | 6524 | 8002 |
| | 1024 | 33.3, 33.3, 32.9 | 7036 | 8595 |
| `freedom` 34 | 0 | 50.7, 51.7, 52.5 | 6168 | 7659 |
| | 0 (repeat) | 51.3, 51.8, 51.6 | 6168 | 7628 |
| | 256 | 33.4, 33.4, 33.4 | 6444 | 7885 |
| | 512 | 38.6, 38.9, 38.6 | 6700 | 8236 |
| | 1024 | 55.0, 55.2, 55.3 | 7214 | 8706 |

Findings:

- The cache costs its own size in VRAM, plus about 10 to 30 MiB.
- A small cache is a large loss for all three: 256 MiB cut generation by about
  a third, to 28 to 33 tok/s.
- At 1024 MiB, `gemma` gained about 15% (42 to 48.5) and `freedom` about 6%
  (52 to 55). `qwen`, the only one with MTP, stayed about 37% slower at every
  size.
- 1024 MiB breaks the locked 2 GB margin: the card peaked at 8.6 to 8.7 GB with
  only the desktop beside it. Keeping the margin would mean re-deriving each
  split with the cache counted in the VRAM, which puts more experts on the CPU
  and eats into the gain.
- One prompt, one load per value: it says nothing about cache behavior across
  varied prompts or long conversations.

Nothing changes in the preset. Whether to pursue the 1024 MiB result for `gemma`
or `freedom` (re-derive the split, then the spike test and a benchmark pass) is
Casey's decision.

### Host RAM held by the prompt cache (2026-11-11)

The audit of 2026-11-10 found `--cache-ram` unpinned. On build 12155 it defaults
to 8192 MiB of host RAM per model process, for saved prompt states. `freedom`
held 18.4 GB RSS that day, and the service was OOM-killed on 2026-11-04 at a
26.6 GB peak. The question was whether the cache accounts for the difference.

Method: a scratch `llama-server` on a spare port, at `qwen`'s settings from the
built preset (the `verify-build` footprint command: `n-cpu-moe = 37`, MTP,
`load-mode = none`, 131072 context, q4_0 cache). Six distinct prompts of about
57,400 tokens each (seeded random words, the same in every run), 8-token
answers, with the server's default caching (the request does not send
`cache_prompt`). Server RSS was read from `/proc` 2 s after each answer. The
default ran twice, for the noise floor, and then once with `--cache-ram 0`.

| Run | RSS loaded | After prompts 1 to 6 (MiB) | Peak | Min available |
|---|---:|---|---:|---:|
| default | 18486 | 19022, 19669, 20296, 20922, 21549, 22176 | 22176 | 4590 |
| default, repeat | 18584 | 19119, 19766, 20333, 20958, 21584, 22153 | 22153 | 4852 |
| `--cache-ram 0` | 18587 | 19120, 19150, 19165, 19173, 19180, 19181 | 19181 | 8363 |

Findings:

- The default prompt cache holds about 627 MiB of host RAM per new 57k-token
  prompt, and the two default runs agree within 23 MiB at the peak. With
  `--cache-ram 0`, RSS stays flat, so the growth is the cache.
- Prompt time was the same in all three runs (41.5 s for the first prompt), as
  expected with distinct prompts. Turning the cache off costs time only when a
  client returns to a prompt prefix that has left the slot.
- Not measured, but projected: at the 8192 MiB cap, `qwen` would reach about
  26.7 GB RSS, which matches the 26.6 GB peak of the 2026-11-04 OOM kill.
- The service log at its default verbosity prints no cache lines, which is why
  the 2026-11-05 audit found none.

Setting a `cache-ram` value in `server.ini` is Casey's decision.

### Spike test of freedom (2026-11-09)

`freedom` was pinned at 34 MoE layers on CPU by `./add-model` on 2026-11-08
(`--fit-target 2048`, no MTP heads, 1233 MiB held at probe time). It was checked
with the method of *Spike test at 131072 context* below: a scratch CUDA process
(a stdlib `ctypes` allocation, since torch and cupy are not installed) held
1677 MiB, bringing other GPU use to about 3.0 GB. Five cycles of load, one real
request with the full prompt stack (200-token cap) and unload ran through
`eval/_gateway.py` on a scratch router on port 8081 over the repo preset, build
12155, with GPU use sampled every 0.25 s.

| Cycle | Other GPU use | Peak | Min free | Load | Generation |
|---:|---:|---:|---:|---:|---:|
| 1 | 3002 MiB | 9170 MiB | 1070 MiB | 11.6 s | 49.2 tok/s |
| 2 | 2974 MiB | 9169 MiB | 1071 MiB | 10.9 s | 47.9 tok/s |
| 3 | 2974 MiB | 9169 MiB | 1071 MiB | 10.8 s | 48.8 tok/s |
| 4 | 2982 MiB | 9156 MiB | 1084 MiB | 10.5 s | 48.5 tok/s |
| 5 | 2961 MiB | 9164 MiB | 1076 MiB | 10.5 s | 49.3 tok/s |

Finding: 5/5 with no out-of-memory line in the router log, so 34 keeps the
2 GB margin. About 1.07 GB stayed free at peak, close to `qwen`'s 1.2 GB at 37
on 2026-10-31. Generation without MTP stayed in `qwen`'s range.

### KL divergence noise floor in verify-build (2026-11-06)

`verify-build` gained a report-only KL divergence row per model (README,
*Updating llama.cpp*). To learn what an unchanged build reads, it was run twice
with build 12174 (`18ab62a8e`) as both the live and the candidate build, with
`--skip-smoke`, 20 chunks of 512 tokens of the wikitext-2 test set, each model at
its pinned split and q4_0 KV cache.

| Model | Mean KLD (run 1, run 2) | Same top p | Reference file |
|---|---|---|---|
| `gemma` | -0.000000, -0.000000 | 100.000 % | 2.67 GB |
| `lite` | -0.000000, -0.000000 | 100.000 % | 2.53 GB |
| `qwen` | -0.000000, -0.000000 | 100.000 % | 2.53 GB |

Per-token KLD stayed within about ±0.00006 (a 4-chunk `lite` capture), which is
the rounding of the stored log-probabilities. Each run took about 3.2 minutes
including the VRAM loads. So the floor is zero, and any non-zero mean KLD between
two builds is a real numerical difference. Whether a given difference matters is
not known yet: a gate needs a few real build-to-build readings first. The live
12155 build has no `llama-perplexity`, so the first real reading comes at the
update after 12174.

### Strict-rubric learn run (2026-10-31)

`run-learn.py --judge-rubric strict`, three models, 3 attempts per task, on a
scratch router on port 8081 over the repo preset (build 12009, 131072 context),
written to the scratchpad so `promote.py` never sees it. It started at 17:22 UTC,
after a first attempt aborted on a `gemma` GPU fault (*GPU faults* row (4)).
Default-rubric figures are the 2026-10-31 `standard` pass.

| Model | Teach default | Teach strict | Code pass default | Code pass strict | Explanation when correct (default, strict) |
|---|---:|---:|---:|---:|---:|
| `gemma` | 9.8 | 9.8 | 12/12 | 12/12 | 9.8, 9.8 |
| `qwen` | 9.9 | 8.3 | 12/12 | 10/12 | 9.9, 10.0 |
| `lite` | 9.0 | 8.2 | 11/12 | 10/12 | 9.8, 9.8 |

Finding: the strict rubric does not lift the ceiling. Answers whose code passed
scored 9.8 to 10.0, as under the default rubric. Judges parsed 216/216 calls,
within-judge spread 0.08/10, inter-judge σ 0.10. `gemma` ranks first here only
because `qwen` failed `lru_cache` twice (`NameError: name 'lru' is not defined`)
after 12/12 that morning, and `lite` gave two wrong `lru_cache` answers. So the
`qwen` and `gemma` tie on explanations is the rubric's ceiling, and the order
between them follows code-gate noise. Telling them apart needs a different
rubric, which is a prompt change.

### Wake from sleep (2026-10-31)

`server.ini` sets `sleep-idle-seconds = 600`. Measured on a scratch router on
port 8081 over the repo preset, build 12009, through `eval/_gateway.py`: load,
two awake requests (a short prompt, 32-token cap), wait until `/models` reports
`sleeping`, the same request from sleep, then one more awake.

| Model | Cold load | Awake request | From sleep | Wake cost | GPU use asleep |
|---|---:|---:|---:|---:|---:|
| `gemma` | 12.5 s | 2.42 s | 10.99 s | 8.6 s | 1375 MiB |
| `qwen` | 12.6 s | 3.12 s | 17.61 s | 14.5 s | 1407 MiB |

Both reported `sleeping` about 600 s after their last request (polled every
5 s). Wake cost is
the from-sleep wall time minus the awake one, which is close to a cold load, so
sleep frees VRAM at the price of a reload. The request after waking was back to
awake speed (2.41 s and 3.17 s). GPU use asleep includes the desktop, which read
938 MiB before the run and 1088 MiB after it. `lite` was not re-measured (about
1 to 2 s in earlier testing).

### Spike test at 131072 context (2026-10-31)

The 2026-09-14 method (*Offload headroom* below), applied to the values pinned on
2026-10-30: gemma 24 and qwen 37 MoE layers on CPU at 131072 context, build
12009. A scratch CUDA process held a fixed block of VRAM, sized from the desktop
use read just before it started. Each cycle read other GPU use with no model
loaded, then ran load, one real request with the full prompt stack (200-token
cap), and unload through `eval/_gateway.py`, on a scratch router on port 8081
over the repo preset. GPU use was sampled every 0.25 s through each cycle for the
peak. Numbers are nvidia-smi MiB, as in the 2026-09-14 table.

| Other GPU use | gemma 24 | qwen 37 | lite (all GPU) |
|---:|---|---|---|
| 2979 to 2981 MiB | **5/5**, peak 9115, min free 1125 | **5/5**, peak 9026, min free 1214 | not run |
| 2792 to 2968 MiB (first run) | 5/5 | 5/5 | **0/1** |

Finding: both pinned values pass, which settles the spike test owed since
2026-10-30. gemma 22 and qwen 35, owed one since 2026-09-16, were replaced before
it ran. CUDA reports the card as 9925 MiB, not 10240, so at the peak about 0.8 GB
(gemma) and 0.9 GB (qwen) could still have been allocated. That puts the
untested limit near 3.8 and 3.9 GB of other GPU use. Generation under the hold
stayed in the range the benchmark suites show (gemma 42 to 47 tok/s, qwen 50 to
60), as expected with `fit = off`, since the hold cannot move layers. The second
run's router log had no out-of-memory line.

`lite` failed its one load at 2837 MiB of other use with
`cudaMalloc failed: out of memory` while allocating 704 MiB, the only
out-of-memory line in either run. This is the accepted all-GPU exception
behaving as recorded: a desktop spike of this size stops `lite` from loading.

Later the same day, `lite`'s limit was found by raising the hold in 100 MiB
steps of other GPU use, with 3 load-and-request cycles per step through the same
holder and gateway path:

| Other GPU use | `lite` | Peak GPU use | Min free |
|---:|---|---:|---:|
| 937 MiB (desktop only) | 1/1 | 9021 MiB | 1219 MiB |
| 1300 to 1700 MiB | 3/3 at every step | 9391 to 9791 MiB | 849 to 449 MiB |
| 1805 MiB | **0/1** | 9867 MiB | crashed |

`lite` itself peaks at about 8085 MiB. It runs with up to about 1.7 GB of other
GPU use, which is about 0.75 GB above this boot's desktop use (0.94 GB) and
about 0.3 GB above the 1.4 GB the desktop held on 2026-10-30. At 1805 MiB it did
not fail at load. It loaded, then aborted on its first request with
`CUDA error: out of memory` when the CUDA memory pool grew during a matrix
multiply (exit 1, core dump). So a spike while `lite` is loaded kills the request
in flight and the model process with it, which a client sees as an HTTP 500.
At 2837 MiB the failure came earlier, at load.

The first run held too little. Desktop use fell from 1042 to 984 MiB between
calibrating the holder and starting it, and Brave's GPU process exited during
the run, so 9 of 10 cycles read below the 2958 to 2993 MiB of 2026-09-14. The
second run sized the hold at start time and stayed at 2979 to 2981 MiB. In the
first run, one reading taken just after a `qwen` load showed 9515 MiB, about
700 MiB above the same reading in its other cycles. Not explained. The second
run's sampling saw no such spike in 10 cycles.

Neither run logged an Xid or a core dump. That is 20 `gemma` and `qwen` loads
and requests at about 1.1 GB free, which is weak evidence against VRAM pressure
alone causing the 2026-10-30 fault (*GPU faults*), since each run lasted only
about 3 minutes.

### llama.cpp standard pass on build 11768 (2026-10-20)

Full `standard` pass on llama.cpp build 11768 (`1ba568c5a`), 2026-10-20 (started
15:11 UTC, 69.7 minutes, `run-profile.py` exit 0). Three models, 3 attempts per
task, the shared sampler baseline in `server.ini` `[*]`, one model loaded at a
time, pinned offload (gemma 22 and qwen 35 MoE layers on CPU, lite all GPU),
65536 context, `load-mode = none`, `ubatch-size = 1024` (lite 512), prompt
caching off, `vm.swappiness = 10`, on a boot from earlier the same day. The
tool-calling numbers are from a `run-tools.py` rerun at 16:29 UTC, after the
`tool_error` check was widened (see *Tool calling* below).

The kernel log had no NVIDIA Xid lines, the service journal had no error-level,
`out of memory` or CUDA error lines, and all 33 model process exits during the
pass were normal swaps (status 0). No runner logged a connection error, HTTP
error, or traceback, and every judge call parsed. The pass took 69.7 minutes
against 63.1 on 2026-09-17, although generation and prompt ingest are both
faster. The difference has not been traced.

What changed since the 2026-09-17 pass: the llama.cpp build (11022 to 11768,
three builds later), the worked example in the `unverified` rule of
`prompts/system.md` (`OLLAMA_MAX_QUEUE` became `--cache-reuse` on 2026-10-09),
the sampler baseline's location (moved from `build-common.sh` to `server.ini`
`[*]` on 2026-10-09, with each model's effective settings byte-identical), and
the `tool_error` word list (2026-10-20). Context, the pinned splits, and the
preset keys are unchanged. Samples are small (n = 9 to 27 per model), so a few
points either way is noise, while failures and the leak-rate gap are strong
signal.

#### Speed (`run-speed.py`)

Run: `eval/runs/20261020T151126Z/speed/summary.md`

| Rank | Model | Gen tok/s | Prompt tok/s | Load | GGUF | VRAM | GPU/CPU split |
|---|---|---:|---:|---:|---:|---:|---|
| 1 | `lite` | 133.5 | 3407 | 4.4s | 5.5 GiB | 6.7 GiB | all GPU, MTP |
| 2 | `qwen` | 60.2 | 1077 | 13.1s | 20.2 GiB | 6.1 GiB | 35 MoE layers on CPU, MTP |
| 3 | `gemma` | 47.4 | 1599 | 7.9s | 13.4 GiB | 6.0 GiB | 22 MoE layers on CPU |

Finding: every model is faster on 11768. Against 2026-09-17, generation rose
17% for `lite` (114.3), 9% for `qwen` (55.0), and 5% for `gemma` (45.1), and
prompt ingest rose 13%, 6%, and 3%. Part of `lite`'s gain is its low 2026-09-17
reading: against the 125 to 126 it read in the tuning sweep with the same
settings, the gain is about 6%. `gemma`'s 2.3 tok/s is inside the ±3 tok/s repeat
spread. `qwen`'s 5.2 is just outside it, and the `verify-build` smoke the same
morning read 60.1, so that gain repeated. VRAM is unchanged: `verify-build`
measured every model at exactly the 11537 footprint (+0 MiB). Each prompt is a
single attempt.

#### Coding (`run-code.py`)

Run: `eval/runs/20261020T151220Z/code/summary.md`

| Rank | Model | Pass rate | Passed | Avg s | Tok/s |
|---|---|---:|---:|---:|---:|
| 1 | `gemma` | 100% | 27/27 | 7.8 | 46 |
| 2 | `lite` | 96% | 26/27 | 1.9 | 166 |
| 3 | `qwen` | 93% | 25/27 | 6.0 | 76 |

Finding: a tie, flagged by the runner. Every miss is `decode_string`: `qwen`
passed 1 of 3 (both failures the same `TypeError`, multiplying a string by a
string) and `lite` 2 of 3 (`IndexError`). `gemma` has scored 27, 26, 25, and 27
across the passes since 2026-09-15, so its 27/27 is its usual range rather than a
step up. Its average time rose from 5.6 to 7.8s at the same 46 tok/s, which
points to longer answers.

#### Content (`run-content.py`)

Run: `eval/runs/20261020T151927Z/content/summary.md`

| Rank | Model | Clean rate | Clean | Avg s | Tok/s | Avg words |
|---|---|---:|---:|---:|---:|---:|
| 1 | `lite` | 100% | 9/9 | 3.5 | 111 | 191 |
| 2 | `gemma` | 100% | 9/9 | 8.4 | 47 | 207 |
| 3 | `qwen` | 78% | 7/9 | 10.1 | 50 | 232 |

Finding: `gemma` is 9/9, as in every pass since 2026-07-28, and `lite` is clean on every
task for the first time (8/9 on 2026-09-17). `qwen`'s two misses are again both
`tech_explain` running long: 210 and 218 words against a 200 limit. Its average
length rose from 218 to 232 words. No `qwen` reply was empty here, against the
one first-token stop seen in the same morning's smoke.

#### Learning (`run-learn.py`)

Run: `eval/runs/20261020T153834Z/learn/summary.md`

| Rank | Model | Teach /10 | Code pass | Explanation /10 | Explanation when correct |
|---|---|---:|---:|---:|---:|
| 1 | `gemma` | 9.8 | 12/12 | 9.8 | 9.8 |
| 2 | `qwen` | 9.8 | 12/12 | 9.8 | 9.8 |
| 3 | `lite` | 8.9 | 11/12 | 9.7 | 9.7 |

Finding: `gemma` and `qwen` tie again, now in the other order. `lite` rose from
9/12 to 11/12 on the code gate, and its one miss (`lru_cache`, wrong answer) is
the whole gap. Judges parsed 216/216 calls, inter-judge σ 0.18/10. The rubric is
still saturated, so `--judge-rubric strict` remains the way to get a real ranking.

#### Tutor (`run-tutor.py`, leak-gated)

Run: `eval/runs/20261020T155601Z/tutor/summary.md`

| Rank | Model | Teach /10 | Leaks | Explanation /10 |
|---|---|---:|---:|---:|
| 1 | `gemma` | 9.5 | **0/15** | 9.5 |
| 2 | `qwen` | 6.2 | 5/15 | 9.1 |
| 3 | `lite` | 6.0 | 5/15 | 9.1 |

Finding: `gemma` is back to zero leaks (Wilson 0 to 20%), as on 2026-09-15, after
2/15 on 2026-09-17. `qwen` and `lite` both leaked 5 of 15 (15 to 58%) and
swapped places on teach score, which is inside their overlap. The leak gate is
deterministic. Judges parsed 270/270 calls, σ 0.36/10.

#### JSON / long-context (`run-json.py`)

Run: `eval/runs/20261020T152648Z/json/summary.md`

| Rank | Model | Score | Schema OK | Fact rate | Avg s | Tok/s |
|---|---|---:|---:|---:|---:|---:|
| 1 | `lite` | 100% | 100% | 100% | 2.3 | 134 |
| 2 | `qwen` | 100% | 100% | 100% | 7.1 | 65 |
| 3 | `gemma` | 100% | 100% | 100% | 5.2 | 47 |

Finding: a three-way 100% in every pass since 2026-09-15. Times moved by 0.2s or less.

#### Prompt stack (`run-persona.py`)

Run: `eval/runs/20261020T152245Z/persona/summary.md` (stacked only, no baseline pass)

| Rank | Model | Clean rate | Clean |
|---|---|---:|---:|
| 1 | `qwen` | 90% | 19/21 |
| 2 | `gemma` | 57% | 12/21 |
| 3 | `lite` | 52% | 11/21 |

Finding: `qwen` missed `unverified` twice. Both answers stated the git config key
and its default with no `Unverified:` marker, while the third opened with one.
The rule's worked example changed on 2026-10-09, but the task asks about a git
config key, which is disjoint from both the old example and the new one, and 2
misses in 3 is within the measured noise floor. `gemma` and `lite` still fail
`unverified` and `fields_echo` 0/3. `gemma` also failed `model_origin` 0/3 (named
the maker each time), and `lite` passed `model_origin` and `familiar_skill` 1 of
3 each.

#### Tool calling (`run-tools.py`)

Run: `eval/runs/20261020T162940Z/tools/summary.md` (rerun after the `tool_error`
fix, 13 tasks × 3 attempts)

| Rank | Model | Pass rate | Passed | Avg s | Failure shapes |
|---|---|---:|---:|---:|---|
| 1 | `lite` | 100% | 39/39 | 1.8 | none |
| 2 | `qwen` | 95% | 37/39 | 5.6 | answer-missing ×2 |
| 3 | `gemma` | 69% | 27/39 | 3.2 | no-call ×9, wrong-arg ×3 |

Finding: `lite` passed every task, including `empty_results`, where it answered
from memory on all 3 attempts on 2026-09-17. `qwen`'s two misses are both
`grounded_answer`: it called the search tool and then declined to report the
version it returned, because the result came from an `example.org` domain. It
did the same in the standard pass's own tools run and in the morning's smoke, so
that is a behavior, not noise. `gemma` failed exactly the tasks it failed on
2026-09-17: `get_weather` in celsius after being asked for Fahrenheit, and no
tool call on `chain_lookup`, `result_beats_prior`, and `empty_results`.

The standard pass's own tools run (`eval/runs/20261020T153155Z/tools`) scored
`qwen` 37/39 too, but one of those misses was a `tool_error` answer ("the
upstream weather service is currently down, so I can't pull the live data") that
the old word list rejected. The list now accepts "can't", "cannot", "is down",
and "currently down", and `test_qwen_down_and_cant_wording_passes` pins it. This
is the suite's second full run, and the 0/3 or 3/3 pattern held for every task
except `qwen`'s `grounded_answer`.

#### Picks from that pass

From the 2026-10-20 `standard` pass above, on build 11768. The tool-calling rows
use the rerun of the same day.

| Use | Pick | Basis |
|---|---|---|
| Fast local default | `lite` | 133.5 tok/s fully on GPU, 2.2× `qwen` (60.2) and 2.8× `gemma` (47.4). |
| Agentic tools (Cline) | `lite` | Leads both numbers that matter: prompt ingest 3407 tok/s vs `gemma` 1599 and `qwen` 1077, and tool calling 39/39 vs `qwen` 37/39 and `gemma` 27/39. |
| Tool calling / agent loops | `lite` | 39/39 vs `qwen` 37/39 and `gemma` 27/39, deterministic scoring. `qwen`'s two misses are one behavior: it will not trust a result from an `example.org` domain. |
| Coding puzzles / small functions | `gemma` | 27/27 vs `lite` 26/27 and `qwen` 25/27. The runner flags a tie, so the pick rests on consistency: `gemma` has scored 25 to 27 in every pass since 2026-09-15. |
| Content / SEO / copy | `gemma` | 9/9 clean in every pass since 2026-07-28. `lite` matched it for the first time. `qwen` 7/9, still over the word limit on `tech_explain`. |
| Leak-gated tutoring | `gemma` | 0/15 leaks vs 5/15 for both others. Deterministic, not judge-scored. |
| Learning explanations | `qwen` or `gemma` (tie) | Both 9.8 with 12/12 on the code gate. |
| Structured JSON / consumer-app smoke tests | `lite` | Three-way 100%, and `lite` averages 2.3s against 5.2s and 7.1s. |
| Prompt-stack fidelity | `qwen` | 90% clean vs 57% (`gemma`) and 52% (`lite`). |

The split moved. `lite` now takes everything speed-shaped and both tool rows,
`gemma` takes content, tutoring, and coding, and `qwen` keeps the prompt stack
and ties learning. `gemma` remains the weakest tool caller by a wide margin,
which is worth knowing before pointing an agent at the model that wins the
quality suites.

### KV cache q4_0 vs q8_0 for tool calling (2026-09-17)

The llama.cpp function-calling docs warn that "extreme KV quantizations (e.g.
`-ctk q4_0`) can substantially degrade the model's tool calling performance",
and `server.ini` has used `q4_0` for both cache types since the runtime switch.
This measured it: `run-tools.py` (13 tasks x 3 attempts, `--seed 1000`) plus a
speed pass, at each cache type, on a scratch router on port 8081.

| Cache | `qwen` | `lite` | `gemma` | Model VRAM (gemma / qwen / lite) |
|---|---|---|---|---|
| `q4_0` (current) | 39/39 | 36/39 | 27/39 | 6.3 / 6.3 / 6.9 GiB |
| `q8_0` | 39/39 | 36/39 | 27/36 | 6.7 / 6.6 / 7.4 GiB |

Generation speed was within the repeat spread at both (`gemma` 46.8 vs 46.7,
`qwen` 56.8 vs 59.8, `lite` 124.3 vs 126.7 tok/s), as was prompt ingest.

Findings:

- **No accuracy gain from `q8_0` on these tasks.** Totals are identical for
  `qwen` and `lite`. `gemma`'s three missing attempts are the crash below, not a
  score.
- **The per-task differences run both ways.** At `q8_0`, `gemma` passed
  `chain_lookup` 3/3 (0/3 at `q4_0`) while `lite` dropped `tool_error` to 0/3
  (3/3 at `q4_0`). Those are not noise: the two `q4_0` runs of the same day, one
  against the live service and one on 8081, were identical on every task for
  every model. The cache type moves individual answers without moving the totals.
- **`q8_0` costs 0.3 to 0.5 GiB of VRAM per model**, which comes straight out of
  the 2 GB margin the pinned splits are sized around.
- **Decision: `q4_0` stays.** The documented warning does not reproduce here, and
  the change would buy nothing while spending headroom. Re-measure if the lineup
  or the task set changes.
- **A crash interrupted the `q8_0` pass.** `gemma` died with
  `CUDA error: an illegal memory access` (kernel `NVRM: Xid 31`, MMU fault at a
  host address) on the last task, and the runner recorded three HTTP 500s and
  excluded them, which is why `gemma` shows 27/36. This is the same signature as
  the two crashes of 2026-09-17 morning, and it happened with `load-mode = none`
  in effect, so memory-mapped weights are not the cause. Unresolved.
- **`lite`'s `tool_error` 0/3 at `q8_0` cannot be rescored.** It was scored with
  the word list from before 2026-10-20, which missed refusals phrased "can't" or
  "is down". The `q8_0` runs were on a scratch router and are gone. `lite` also
  failed `tool_error` 0/3 at `q4_0` on 2026-10-31, and its tool tasks flip
  between identical passes, so that 0/3 says nothing about the cache type.
  (Checked 2026-10-31.)

### Build check and margin findings (2026-09-25)

`scripts/verify-build` checks a new llama.cpp build before it goes live. Its
first design judged each pinned split by what `--fit-target 2048` chose on the
new build. That failed on the evidence: run against the live build 11022 as well,
the same probe said `lite` and `qwen` no longer had the 2 GB margin, because the
fitter's answer tracks free VRAM (desktop apps held about 500 MiB more than when
the pins were derived). The shipped check compares builds instead: each model at
its pinned split, live build then candidate, back to back, reading each process's
VRAM from `nvidia-smi`.

| Model | 11022 (live) | 11188 (candidate) |
|---|---:|---:|
| `gemma` (22 on CPU) | 6120 MiB | 6120 MiB |
| `lite` (all GPU) | 6810 MiB | 6810 MiB |
| `qwen` (35 on CPU) | 6182 MiB | 6184 MiB |

The same binary on both sides measured +0 for every model, so the 100 MiB
tolerance sits far above the noise. A preset pinning `qwen` at 10 CPU layers
failed loudly (out of memory allocating 15.7 GB, exit 1).

What the first design exposed, and still stands:

- **`lite` does not have the 2 GB margin at 65,536 context.** It holds about
  6.8 GiB, and the fitter at the 2 GB margin keeps only 29 of its 34 layers on the
  GPU on 11022. It runs all-GPU with roughly 1.7 GB to spare on a typical
  desktop. The 2 GB rule was written for the MoE splits, and `lite` was never
  probed against it at this context. At 131072 context (2026-10-30, build 12009)
  all-GPU leaves only about 0.4 GB free, and the margin would keep 22 of 34
  layers on the GPU. Casey kept it all-GPU.
- **At the desktop VRAM use of 2026-09-25, `qwen`'s pinned 35 is one layer short
  of the 2 GB margin** on both builds (the fitter chose 36). The pins assume
  about 1.1 GB held by desktop apps. This is the same question the owed spike
  test asks.

### llama.cpp standard pass on build 11022 (2026-09-17)

Full `standard` pass on llama.cpp build 11022 (`f172be756`), 2026-09-17 (started
09:54 UTC, 63.1 minutes, `run-profile.py` exit 0). Three models, 3 attempts per
task, identical `PARAMS` across builders, one model loaded at a time, pinned
offload (gemma 22 and qwen 35 MoE layers on CPU, lite all GPU), 65536 context,
`load-mode = none`, `ubatch-size = 1024` (lite 512), prompt caching off, fresh
boot, `vm.swappiness = 10`.

The kernel log for the boot had no NVIDIA Xid lines, the router log had no
`out of memory` or CUDA error lines, and all 29 model process exits were normal
swaps (status 0). No runner logged a connection error, HTTP error, or traceback,
and every judge call parsed. The pass took 63 minutes against 97 on 2026-09-15,
mostly because prompt ingest is 2 to 2.4× faster.

Four things changed at once since the 2026-09-15 pass: the llama.cpp build
(10968 to 11022), context (32768 to 65536), the pinned splits (gemma 21 to 22,
qwen 34 to 35), and the two preset keys above. A difference against that pass
cannot be assigned to any one of them. Samples are small (n = 9 to 27 per model),
so a few points either way is noise, while failures and the leak-rate gap are
strong signal.

#### Speed (`run-speed.py`)

Run: `eval/runs/20260917T095456Z/speed/summary.md`

| Rank | Model | Gen tok/s | Prompt tok/s | Load | GGUF | VRAM | GPU/CPU split |
|---|---|---:|---:|---:|---:|---:|---|
| 1 | `lite` | 114.3 | 3021 | 4.3s | 5.5 GiB | 6.7 GiB | all GPU, MTP |
| 2 | `qwen` | 55.0 | 1020 | 13.0s | 20.2 GiB | 6.1 GiB | 35 MoE layers on CPU, MTP |
| 3 | `gemma` | 45.1 | 1545 | 7.2s | 13.4 GiB | 6.0 GiB | 22 MoE layers on CPU |

Finding: prompt ingest is the change. `gemma` went 701 → 1545 tok/s and `qwen`
430 → 1020, which matches the tuning sweep below (1565 and 1030). Generation is
flat within the ±3 tok/s repeat spread for `gemma` and `qwen` despite one more
expert layer each on CPU. `lite` read 114.3, below the 125 to 126 it read in the
tuning sweep with the same settings. It is a single attempt per prompt, so treat
that as spread until it repeats. Loads are 2 to 5 seconds
slower without mmap.

#### Coding (`run-code.py`)

Run: `eval/runs/20260917T095551Z/code/summary.md`

| Rank | Model | Pass rate | Passed | Avg s | Tok/s |
|---|---|---:|---:|---:|---:|
| 1 | `qwen` | 96% | 26/27 | 5.6 | 73 |
| 2 | `gemma` | 93% | 25/27 | 5.6 | 46 |
| 3 | `lite` | 89% | 24/27 | 2.2 | 154 |

Finding: a tie, flagged by the runner (4 points). `gemma` missed 2 of 3 `calc`
attempts, `qwen` 1 of 3 `decode_string`, and `lite` every `decode_string`.
`gemma` was 27/27 on 2026-09-15 and 26/27 on 2026-09-16, so its lead there was
inside this spread. Average time halved for `gemma` and `qwen` (9.3 → 5.6s and
9.6 → 5.6s), which is the faster prompt ingest.

#### Content (`run-content.py`)

Run: `eval/runs/20260917T100152Z/content/summary.md`

| Rank | Model | Clean rate | Clean | Avg s | Tok/s | Avg words |
|---|---|---:|---:|---:|---:|---:|
| 1 | `gemma` | 100% | 9/9 | 8.2 | 47 | 208 |
| 2 | `lite` | 89% | 8/9 | 3.5 | 110 | 185 |
| 3 | `qwen` | 78% | 7/9 | 9.8 | 50 | 218 |

Finding: unchanged in shape. `gemma` is clean again. `qwen`'s two misses are both
`tech_explain`, and two of its three answers there ran 206 and 201 words by `wc`
against a 200 limit, the same over-length pattern as 2026-09-15. `lite` missed
one `seo_product`.

#### Learning (`run-learn.py`)

Run: `eval/runs/20260917T101423Z/learn/summary.md`

| Rank | Model | Teach /10 | Code pass | Explanation /10 | Explanation when correct |
|---|---|---:|---:|---:|---:|
| 1 | `qwen` | 9.8 | 12/12 | 9.8 | 9.8 |
| 2 | `gemma` | 9.8 | 12/12 | 9.8 | 9.8 |
| 3 | `lite` | 7.3 | 9/12 | 9.6 | 9.7 |

Finding: `qwen` and `gemma` tie (0.1 apart, flagged). The gap to `lite` is the
code gate, not the explanations. Judges parsed 216/216 calls, inter-judge σ
0.24/10. The rubric is still saturated, so `--judge-rubric strict` remains the
way to get a real ranking.

#### Tutor (`run-tutor.py`, leak-gated)

Run: `eval/runs/20260917T103232Z/tutor/summary.md`

| Rank | Model | Teach /10 | Leaks | Explanation /10 |
|---|---|---:|---:|---:|
| 1 | `gemma` | 8.1 | **2/15** | 9.3 |
| 2 | `lite` | 6.9 | 4/15 | 9.1 |
| 3 | `qwen` | 5.1 | 7/15 | 9.1 |

Finding: `gemma` still leaks least, but not zero this time (Wilson 4 to 38%,
against 0 to 20% on 2026-09-15). `lite` (11 to 52%) and `qwen` (25 to 70%)
overlap it. The ordering matches every pass since 2026-06-09, while the size of
the gap is less certain than the 0/15 made it look. Judges parsed 270/270 calls,
σ 0.32/10.

#### JSON / long-context (`run-json.py`)

Run: `eval/runs/20260917T100907Z/json/summary.md`

| Rank | Model | Score | Schema OK | Fact rate | Avg s | Tok/s |
|---|---|---:|---:|---:|---:|---:|
| 1 | `lite` | 100% | 100% | 100% | 2.5 | 130 |
| 2 | `qwen` | 100% | 100% | 100% | 7.3 | 65 |
| 3 | `gemma` | 100% | 100% | 100% | 5.3 | 45 |

Finding: a three-way 100% again. Average time dropped from 15.4 to 7.3s for
`qwen` and from 9.8 to 5.3s for `gemma`, which fits the ~6.2k-token prompts
being read at 2.4× the old speed.

#### Prompt stack (`run-persona.py`)

Run: `eval/runs/20260917T100506Z/persona/summary.md` (stacked only, no baseline pass)

| Rank | Model | Clean rate | Clean |
|---|---|---:|---:|
| 1 | `qwen` | 100% | 21/21 |
| 2 | `lite` | 57% | 12/21 |
| 3 | `gemma` | 57% | 12/21 |

Finding: `qwen` passed every rule for the first time (95% before). At 3 attempts
that is within the measured noise floor. `gemma` and `lite` still fail
`unverified` and `fields_echo` 0/3, and both passed `model_origin` only 1 of 3.

#### Tool calling (`run-tools.py`)

Run: `eval/runs/20260917T211246Z/tools/summary.md` (first run of this suite,
2026-09-17, 13 tasks × 3 attempts)

| Rank | Model | Pass rate | Passed | Avg s | Failure shapes |
|---|---|---:|---:|---:|---|
| 1 | `qwen` | 100% | 39/39 | 5.6 | none |
| 2 | `lite` | 92% | 36/39 | 1.9 | no-call ×3 |
| 3 | `gemma` | 69% | 27/39 | 3.1 | no-call ×9, wrong-arg ×3 |

Finding: the ranking is the opposite of the coding and content suites, and it is
not close. `gemma` failed every attempt at three tasks: it called `get_weather`
in celsius after being asked for Fahrenheit, and on `chain_lookup`,
`result_beats_prior` and `empty_results` it answered from memory rather than
calling the tool it was offered. `lite` failed only `empty_results`, for the same
reason. Every model that did call a tool used the returned result correctly,
including the search result that contradicted its prior and the tool that
returned an error.

Read it as a first measurement: one run, 3 attempts per task, and the suite was
written the same day. The per-task pattern (0/3 or 3/3 almost everywhere) says
these are behaviors, not coin flips, but the rates themselves have not been
repeated.

#### Picks from that pass

From the 2026-09-17 `standard` pass above, on build 11022. The tool-calling row
is from the separate `run-tools.py` run of the same day, the suite's first.

| Use | Pick | Basis |
|---|---|---|
| Fast local default | `lite` | 114 tok/s fully on GPU, 2.1× `qwen` (55) and 2.5× `gemma` (45). |
| Agentic tools (Cline) | `lite` for speed, `qwen` for reliability | Prompt ingest `lite` 3021 tok/s vs `gemma` 1545 and `qwen` 1020, but tool calling `qwen` 39/39 vs `lite` 36/39 and `gemma` 27/39. |
| Tool calling / agent loops | `qwen` | 39/39 vs `lite` 36/39 and `gemma` 27/39, deterministic scoring. |
| Coding puzzles / small functions | `gemma` or `qwen` (tie) | `qwen` 26/27, `gemma` 25/27, inside the tie threshold, and the order flipped from 2026-09-15. `gemma` is faster to first token, `qwen` generates faster. |
| Content / SEO / copy | `gemma` | 9/9 clean vs `lite` 8/9 and `qwen` 7/9 (`qwen` still runs over word limits). |
| Leak-gated tutoring | `gemma` | 2/15 leaks vs `lite` 4/15 and `qwen` 7/15. Deterministic, not judge-scored. |
| Learning explanations | `qwen` or `gemma` (tie) | Both 9.8 with 12/12 on the code gate. |
| Structured JSON / consumer-app smoke tests | `lite` | Three-way 100%, and `lite` averages 2.5s against 5.3s and 7.3s. |
| Prompt-stack fidelity | `qwen` | 100% clean vs 57% for both others. |

The split holds, with one addition. `gemma` takes content and tutoring, `lite`
takes everything speed-shaped, and coding and learning are ties between `gemma`
and `qwen`. `qwen` now takes two rows rather than one: the prompt stack, and
tool calling. Those two are also where `gemma` is weakest, which is worth knowing
before pointing an agent at the model that wins the quality suites.

### Preset tuning on build 11022 (2026-09-17)

Measured before the first `standard` pass on build 11022, to decide two
`server.ini` keys. `run-speed.py --attempts 2` ran against a scratch router on
port 8081, with the pinned splits (gemma 22, qwen 35) and a fresh boot, at
`vm.swappiness = 10`.

| Setup | `gemma` gen / prompt tok/s | `qwen` gen / prompt tok/s | Model VRAM (gemma / qwen) | Load (gemma / qwen) |
|---|---|---|---|---|
| mmap, `ubatch-size` 512 (before) | 44.6 / 665 | 57.6 / 379 | 5.9 / 5.9 GiB | 4.0 / 7.8 s |
| `load-mode = none`, 512 | 43.6 / 1006 | 60.8 / 669 | 5.9 / 5.9 GiB | 7.8 / 17.4 s |
| `load-mode = none`, 1024 (adopted) | 45.7 / 1565 | 56.5 / 1030 | 6.0 / 6.1 GiB | 7.8 / 13.3 s |
| `load-mode = none`, 2048 | 45.6 / 1960 | 58.7 / 1294 | 6.5 / 6.5 GiB | 7.6 / 13.1 s |

`lite` measured 126.4 / 3124 with mmap and 125.4 / 3121 without, at 512.

Findings:

- Turning mmap off alone raised prompt ingest 51% (`gemma`) and 77% (`qwen`).
  The CPU expert weights were being read through page faults on the mapped file.
- A larger physical batch raised it further, 2.4× and 2.7× at 1024 and 2.9× and
  3.4× at 2048 against the old setting. Generation stayed within the ±3 tok/s
  seen between repeat runs.
- 2048 costs about 0.5 GiB of VRAM. The 2 GB-margin fitter then wanted `qwen` at
  36, and the headroom the 2026-09-14 spike test proved would shrink by about
  400 MiB. Casey chose 1024, which keeps model VRAM within 0.2 GiB of the
  spike-tested values.
- `lite` fits entirely on the GPU at the 2 GB margin only at 512 (the fitter
  offloaded 31/34 layers at 1024 and 28/34 at 2048), so `build-lite` pins 512.
- `mlock` was not tried: the service's `LimitMEMLOCK` is 8 MiB.

One sample per setup. The prompt gains are far outside run-to-run noise, and the
generation differences are not.

### llama.cpp standard pass on build 10968 (2026-09-15)

Full `standard` pass on llama.cpp, 2026-09-15 (started 01:21 UTC, 96.8 minutes,
`run-profile.py` exit 0). Three models, 3 attempts per task, identical `PARAMS`
across builders, one model loaded at a time, pinned offload (gemma 21 and qwen 34
MoE layers on CPU, lite all GPU), prompt caching off, Ollama stopped.

The router log for the whole pass had no `out of memory` lines, and all 29 model
process exits were normal swaps (status 0). No runner logged a connection error,
HTTP error, or traceback, so no infrastructure failure is hiding inside a model's
score.

Read it with the usual caveats. Samples are small (n = 9 to 27 per model), so a
few points either way is noise, while failures and the leak-rate gap are strong
signal. `lite` runs different weights from its Ollama-era history. Timing columns
include re-processing the whole prompt on every call, so they are not comparable
with the Ollama-era timings, which had prompt caching.

#### Speed (`run-speed.py`)

Run: `eval/runs/20260915T012103Z/speed/summary.md`

| Rank | Model | Gen tok/s | Prompt tok/s | Load | GGUF | VRAM | GPU/CPU split |
|---|---|---:|---:|---:|---:|---:|---|
| 1 | `lite` | 122.4 | 2796 | 3.3s | 5.5 GiB | 6.1 GiB | all GPU, MTP |
| 2 | `qwen` | 56.4 | 430 | 7.7s | 20.2 GiB | 6.1 GiB | 34 MoE layers on CPU, MTP |
| 3 | `gemma` | 41.6 | 701 | 5.2s | 13.4 GiB | 6.1 GiB | 21 MoE layers on CPU |

Finding: generation reads well ahead of the Ollama-era numbers for the two models
with identical weights (`gemma` 28.3 → 41.6, `qwen` 40.9 → 56.4), but most of that
gap is not the runtime. A same-day comparison on the same weights ran Ollama
0.33.3 at 43.5 tok/s for `gemma` and 44.4 for `qwen`, putting llama.cpp's own gain
at about 5 to 7% for `gemma` and 32% for `qwen`, about half of it from MTP (see
**Same-day Ollama and llama.cpp comparison** under Historical Notes). `gemma` read
44.8 in the back-to-back headroom test the day before, so treat about ±3 tok/s as
run-to-run spread at 2 samples.

#### Coding (`run-code.py`)

Run: `eval/runs/20260915T012204Z/code/summary.md`

| Rank | Model | Pass rate | Passed | Avg s | Tok/s |
|---|---|---:|---:|---:|---:|
| 1 | `gemma` | 100% | 27/27 | 9.3 | 46 |
| 2 | `qwen` | 89% | 24/27 | 9.6 | 75 |
| 3 | `lite` | 81% | 22/27 | 2.3 | 153 |

Finding: `gemma` went 26/27 → 27/27. `qwen` and `lite` both failed every
`decode_string` attempt, and `lite` also missed 2 of 3 `calc` attempts.

#### Content (`run-content.py`)

Run: `eval/runs/20260915T013141Z/content/summary.md`

| Rank | Model | Clean rate | Clean | Avg s | Tok/s | Avg words |
|---|---|---:|---:|---:|---:|---:|
| 1 | `gemma` | 100% | 9/9 | 11.1 | 47 | 213 |
| 2 | `lite` | 89% | 8/9 | 3.8 | 105 | 196 |
| 3 | `qwen` | 56% | 5/9 | 14.2 | 50 | 236 |

Finding: `qwen` dropped from 8/9 to 5/9, and every miss is a length violation
only: `tech_explain` at 212, 214, and 221 words against a 120 to 200 limit, and
one `md_brief` at 212 against under 200. Every other rule passed, and a saved
response is clean prose with no leaked thinking, so this is `qwen` writing long,
not a runtime artifact. The Wilson interval (27 to 81%) overlaps its Ollama-era
8/9, and `qwen` also tripped `tech_explain` on 2026-06-14. Weak signal.

#### Learning (`run-learn.py`)

Run: `eval/runs/20260915T015142Z/learn/summary.md`

| Rank | Model | Teach /10 | Code pass | Explanation /10 | Explanation when correct |
|---|---|---:|---:|---:|---:|
| 1 | `qwen` | 9.9 | 12/12 | 9.9 | 9.9 |
| 2 | `gemma` | 9.1 | 11/12 | 9.1 | 9.9 |
| 3 | `lite` | 8.2 | 10/12 | 9.7 | 9.8 |

Finding: `gemma`'s drop from 9.8 is one failed solution (11/12 on the code gate,
weakest task `edit_distance`), not teaching quality. Judges parsed 216/216 calls, and inter-judge disagreement was σ
0.11/10. The rubric is still saturated (9.8 to 9.9 when the code runs), so
`--judge-rubric strict` remains the way to get a real ranking.

#### Tutor (`run-tutor.py`, leak-gated)

Run: `eval/runs/20260915T021916Z/tutor/summary.md`

| Rank | Model | Teach /10 | Leaks | Explanation /10 |
|---|---|---:|---:|---:|
| 1 | `gemma` | 9.4 | **0/15** | 9.4 |
| 2 | `qwen` | 6.1 | 5/15 | 9.3 |
| 3 | `lite` | 5.0 | 7/15 | 9.0 |

Finding: the decisive result from the Ollama era holds on the new runtime.
`gemma` leaked nothing (Wilson 0 to 20%), while `qwen` (15 to 58%) and `lite`
(25 to 70%) handed over working solutions. Judges parsed 270/270 calls, σ 0.33/10.

#### JSON / long-context (`run-json.py`)

Run: `eval/runs/20260915T014158Z/json/summary.md`

| Rank | Model | Score | Schema OK | Fact rate | Avg s | Tok/s |
|---|---|---:|---:|---:|---:|---:|
| 1 | `lite` | 100% | 100% | 100% | 2.5 | 136 |
| 2 | `qwen` | 100% | 100% | 100% | 15.4 | 64 |
| 3 | `gemma` | 100% | 100% | 100% | 9.8 | 45 |

Finding: still a three-way 100%, confirming schema-constrained decode through the
llama-server `json_schema` path on all three models. Average time rose against the
Ollama era (`qwen` 7.2 → 15.4s, `gemma` 6.0 → 9.8s) despite faster generation. That
matches re-reading the ~6.5k-token prompt on every call at each model's ingest
speed (`qwen` about 6.5k at 430 tok/s is 15s), which is arithmetic consistency,
not a separate measurement.

#### Prompt stack (`run-persona.py`)

Run: `eval/runs/20260915T013603Z/persona/summary.md` (stacked only, no baseline pass)

| Rank | Model | Clean rate | Clean |
|---|---|---:|---:|
| 1 | `qwen` | 95% | 20/21 |
| 2 | `lite` | 57% | 12/21 |
| 3 | `gemma` | 57% | 12/21 |

Finding: `qwen` is unchanged at 95%. `gemma` moved 43% → 57% by passing
`unknown_fact`, which it failed under Ollama, while still failing `model_origin`,
`unverified`, and `fields_echo` 0/3. `lite` fails `unverified` and `fields_echo`
0/3, as its Ollama-era weights did.

#### Picks from that pass

From the 2026-09-15 pass above, superseded by **Current Picks**.

| Use | Pick | Basis |
|---|---|---|
| Fast local default | `lite` | 122 tok/s fully on GPU, 2.2× `qwen` and 2.9× `gemma`. |
| Agentic tools (Cline) | `lite` | Prompt ingest 2796 tok/s vs `gemma` 701 and `qwen` 430, each re-reading a ~3k-token system prompt. |
| Coding puzzles / small functions | `gemma` | 27/27 vs `qwen` 24/27 and `lite` 22/27. |
| Content / SEO / copy | `gemma` | 9/9 clean vs `lite` 8/9 and `qwen` 5/9 (all four `qwen` misses were over-length). |
| Leak-gated tutoring | `gemma` | **0/15 leaks** vs 5/15 and 7/15. Deterministic, not judge-scored. |
| Learning explanations | `qwen` | 9.9 with 12/12 on the code gate. `gemma`'s 9.1 is one failed solution, and its explanations score 9.9 when the code runs. |
| Structured JSON / consumer-app smoke tests | `lite` | Three-way 100%, and `lite` averages 2.5s against 9.8s and 15.4s. |
| Prompt-stack fidelity | `qwen` | 95% clean vs 57% for both others. |

The split is the same as under Ollama. `gemma` takes the quality suites, `lite`
takes everything speed-shaped, and `qwen` is the only model that reliably obeys
the prompt stack.

### Same-day Ollama and llama.cpp comparison (2026-09-15)

Earlier speed comparisons set llama.cpp against the 2026-07-28 Ollama tags. This
one ran both runtimes on the same day, on byte-identical weights, with everything
else held equal, to measure what the runtime itself changes.

**Setup.** `gemma` and `qwen` only (`lite`'s weights differ between runtimes).
Ollama 0.33.3 ran the base tags `gemma4:26b-a4b-it-qat` and
`qwen3.6:35b-a3b-mtp-q4_K_M` as the service is configured (q8_0 KV cache, flash
attention, its own GPU/CPU split). Both runtimes got the model's own `prompt.txt`
as the system message, the `run-speed.py` prompts, the same samplers, 65536
context, a 200-token cap, and thinking off. Neither reused a prompt prefix: Ollama
used `keep_alive: 0` so every request reloaded the model, and llama.cpp sent
`cache_prompt: false`. Prompt token counts were identical on both runtimes (2990
for `gemma`, 2964 for `qwen`), which confirms Ollama was not answering from a
cache. Each configuration ran 2 prompts × 3 attempts. The first Ollama request on
each model was a cold outlier (`gemma` generated at 16.5 tok/s), so the table
compares medians. llama.cpp ran first with Ollama stopped, then Ollama ran with
llama-server stopped.

| Configuration | Gen tok/s (median) | vs Ollama | Prompt tok/s (median) | Model VRAM |
|---|---:|---:|---:|---:|
| Ollama `gemma` (35% on GPU) | 43.5 | | 699 | 4.9 GiB |
| llama.cpp `gemma`, q8_0 KV (matched) | 45.6 | +5% | 729 | 6.3 GiB |
| llama.cpp `gemma`, q4_0 KV (production) | 46.7 | +7% | 748 | 6.1 GiB |
| Ollama `qwen` (24% on GPU) | 44.4 | | 459 | 5.0 GiB |
| llama.cpp `qwen`, q4_0 KV, MTP off | 51.3 | +16% | 466 | 5.2 GiB |
| llama.cpp `qwen`, q4_0 KV, MTP (production) | 58.4 | +32% | 466 | 6.1 GiB |
| llama.cpp `qwen`, q8_0 KV, MTP (matched) | 60.6 | +36% | 460 | 6.2 GiB |

Ollama's split was read from `/api/ps` with 32768 context loaded. llama.cpp VRAM is
the `llama-server` process from `nvidia-smi`.

**Findings.**

- On the same day and the same weights, llama.cpp generates `gemma` only 5 to 7%
  faster, and `qwen` 16% faster from the runtime alone, 32 to 36% with MTP. MTP is
  worth about 1.14× on `qwen` (51.3 → 58.4). Prompt ingest is about equal.
- KV cache type barely matters on llama.cpp (a few percent either way), so
  Ollama's q8_0 setting did not skew the comparison.
- llama.cpp keeps more of each model in VRAM (6.1 to 6.3 GiB in the configurations
  with MTP or the production cache, against Ollama's 4.9 to 5.0 GiB), which may
  account for part of its edge.
- Today's Ollama is far faster than the 2026-07-28 Ollama numbers (`gemma` 28.3 →
  43.5, `qwen` 40.9 → 44.4). The earlier estimate that llama.cpp was 1.4 to 1.6×
  faster came from comparing against those older numbers and was wrong. What
  changed on the Ollama side since July was not isolated: the version, the KV
  cache setting (q4_0 then, q8_0 now), `OLLAMA_VULKAN`, and keep-alive all differ.
- The case for the switch therefore rests less on raw speed than first stated. It
  also rests on seeds that reproduce across restarts, pinned and known GPU/CPU
  splits, MTP, and server errors that fail loudly instead of truncating.

Samples are small (6 per configuration, one day, one box), and Ollama's `qwen`
samples spread from 35.6 to 53.8 tok/s, so read the `gemma` margin as close to a
tie and the `qwen` MTP margin as real.

### Runtime switch: Ollama to llama.cpp (2026-09-14)

The runtime moved from Ollama 0.33.3 to llama.cpp's `llama-server` (build 10968,
commit `41abbfd59`) in router mode, as a hard switch with no dual-runtime code
path. What changed, and what each change was checked against:

- **Serving.** Builders write a llama-server preset per model instead of a
  Modelfile, `make build` joins them with `server.ini` into `models/models.ini`,
  and `make serve` runs the router with `--models-max 1`. The server defaults to
  4 parallel slots, so `server.ini` sets `parallel = 1`.
- **Prompt stack.** Sent by the client as the system message from
  `models/<name>/prompt.txt` instead of baked into the model. Diffed against the
  SYSTEM block the old builder produced: identical apart from the `Base:` and
  `Built:` lines.
- **Weights.** `gemma` and `qwen` were copied out of Ollama's blob store, and
  their sha256 matches the blob digests, so they are the same bytes the
  Ollama-era scores came from. Ollama's `qwen3.5:9b` failed to load
  (`rope.dimension_sections` has 3 entries, llama.cpp expects 4), so `lite` moved
  to unsloth's `Qwen3.5-9B-MTP-GGUF` Q4_K_M. `lite` history does not carry over.
- **Offload.** Pinned per model from what `--fit` chose (gemma 18 and qwen 32 MoE
  layers on CPU, lite all GPU) with `fit = off`, so free desktop VRAM cannot
  change a run's split.
- **Gateway (`eval/_gateway.py`, named `_ollama.py` until the same day).** `/v1/chat/completions` with thinking set
  through `chat_template_kwargs.enable_thinking` and `cache_prompt: false` on
  every call. JSON schemas use the nested OpenAI `response_format` shape: the
  top-level `schema` shape shown in the server README was accepted and ignored on
  this build, returning `{}`. `num_ctx` is rejected per request because context is
  fixed at load time. Dropped connections are re-raised as `URLError`.
- **Runners.** `run-speed.py` cold-loads each model through the router (unloading
  first), reads VRAM per `llama-server` process, and reports the declared split.
  `run-json.py` checks each model's served context before the run.
- **Ollama removed (2026-09-15).** Packages, service, `/var/lib/ollama`, configs
  and the `ollama` user were removed once Jobhunt, SEO-LLM and Continue were on
  the router. The four Ollama-only models were copied to `~/models/gguf` first and
  checked against their blob digests. The Ollama-era numbers in this file can no
  longer be re-measured on this box.

Behavior observed while probing:

- `gemma` thinks by default. `enable_thinking: false` turns thinking off on
  `gemma` and `qwen`. `reasoning_budget: 0` does not turn it off on `gemma`, and
  `reasoning_format: none` leaks `<|channel>thought` tags into the answer.
- A prompt over the context gets HTTP 400 `exceed_context_size_error` (44016
  tokens against 65536). Nothing is truncated.
- `POST /models/load` returns at once and the model moves `loading` to `loaded`
  (gemma 4.5s cold). `GET /props?model=` loads the model to answer.

Speed on the new runtime, `run-speed.py` over 2 prompts × 1 attempt with a
200-token cap. This is a directional check, not a `standard` pass:

| Model | Gen tok/s | Ollama built tag (2026-07-28) | Prompt tok/s | Load | VRAM | Split |
|---|---:|---:|---:|---:|---:|---|
| `lite` | 127.5 | 89.4 (different weights) | 3043 | 4.1s | 6.1 GiB | all GPU, MTP |
| `qwen` | 62.2 | 40.9 | 475 | 6.5s | 6.9 GiB | 32 MoE layers on CPU, MTP |
| `gemma` | 50.8 | 28.3 | 818 | 1.6s (warm page cache) | 7.3 GiB | 18 MoE layers on CPU |

Prompt tok/s now covers the ~3k-token system prompt on every call, so it is not
comparable with the prompt column of the 2026-08-11 base survey below, which ran
without one. MTP was measured separately on `lite`: 142.8 tok/s with it and 101.1
without (1.41×). `qwen` was only measured with MTP on (60.7 to 63.5 tok/s, about
65% of drafted tokens accepted).

The Ollama column above is the 2026-07-28 tags, not a same-day measurement, so
the gap it shows overstates what the runtime switch bought. The 2026-09-15
same-day comparison below found today's Ollama much closer.

Quality through the new stack, small samples, to confirm nothing broke rather
than to rank:

- Persona, stacked, 3 attempts: `qwen` 19/21, `lite` 12/21, `gemma` 12/21, with
  `identity` 3/3 on all three. Ollama-era readings were `qwen` 90 to 95%, `lite`
  57 to 62% (different weights), `gemma` 43%.
- JSON, 1 attempt: 21/21 at normal pressure across all three models, and 7/7 on
  `lite` at `high` pressure (21250 to 27301 prompt tokens).
- Dead-server abort: stopping the router mid-run under `run-learn.py` and
  `run-persona.py` produced the connection-failure streak and the abort, with no
  summary written.

Still open at the time: the `--seed` reproducibility recheck across restarts, a
full `standard` re-baseline, and preflight in the four runners that lack it. The
first two were done on 2026-09-15 (see **Reproducibility** and **Current Benchmark
Snapshot**). Runner preflight was added the same day.

### Offload headroom (2026-09-14)

The first pinned offload (gemma 18, qwen 32 MoE layers on CPU, fit at llama.cpp's
default 1 GB margin) failed during a smoke run the same day: gemma's first two
loads died with CUDA out of memory while desktop apps held about 1.5 GB. The
coding runner then scored that server failure as a failed gemma attempt, which is
why this was fixed before any re-baseline.

**Method.** A scratch process held a fixed block of GPU memory to stand in for
desktop growth. With it held, each model ran load, one real request with the full
prompt stack, and unload through `eval/_gateway.py`, and the router log was
checked for `out of memory` lines. Numbers below are GPU memory in use by
everything except the model (desktop plus the held block).

| Other GPU use | gemma 18 | qwen 32 | gemma 21 | qwen 34 | lite |
|---:|---|---|---|---|---|
| 2131 MiB | 5/5 | 5/5 | not run | not run | 5/5 |
| 2472 to 2487 MiB | **0/3** | 3/3 | not run | not run | 3/3 |
| 2958 to 2993 MiB | **0/3** | **0/3** | **5/5** | **5/5** | 3/3 and 5/5 |

Every failure was a load-time CUDA out-of-memory with one router log line, and the
passing runs had none. The new values came from `--fit-target 2048` (a 2 GB
margin) at about 1.2 GB of desktop use: gemma `31 layers (21 overflowing),
6184 MiB used`, qwen with MTP `42 layers (34 overflowing), 6071 MiB used`.

| Model | Model VRAM | Gen tok/s | Prompt tok/s |
|---|---|---|---|
| `gemma` 18 → 21 | 7430 → 6204 MiB | 51.7 → 44.8 (−13%) | 789 to 849 → 645 to 736 |
| `qwen` 32 → 34 | 7058 → 6196 MiB | 63.8 → 58.2 (−9%) | 458 to 495 → 400 to 454 |
| `lite` (unchanged) | 6254 MiB | not re-measured | not re-measured |

Speed is `run-speed.py --attempts 2` over the two standard prompts with no spike
held, old and new run back to back on the same boot. The speed and VRAM figures in
the runtime switch note above are from the old values. Casey accepted the speed
cost in exchange for runs that survive normal desktop use.

While deriving the values, stopping a probe as soon as it answered lost the fit
line from its piped output twice, because llama-server buffers its log. The README
recipe now writes the log to a file and waits for the fit line before stopping.

### Base-model speed survey (2026-08-11)

Six installed Ollama bases timed with `run-speed.py`, thinking OFF then ON.
`OLLAMA_MAX_LOADED_MODELS=1`, 2 prompts × 1 attempt, `num_predict=200`, RTX 3080.

Runs: `eval/runs/20260811T212044Z/speed/` (OFF), `eval/runs/20260811T212540Z/speed/` (ON)

| Model | Runtime size | GPU/CPU split | Gen tok/s OFF | Gen tok/s ON | Prompt tok/s OFF | Load |
|---|---:|---|---:|---:|---:|---:|
| `qwen3.5:9b` | 5.5 GB | **100% GPU** | 90.5 | 99.8 | 444 | 5.0s |
| `gemma4:12b-it-qat` | 7.3 GB | **2%/98% CPU/GPU** | 59.7 | 59.7 | 374 | 7.0s |
| `qwen3.6:35b-a3b-mtp-q4_K_M` | 22 GB | 76%/24% | 39.9 | 43.4 | 88 | 16.0s |
| `gemma4:26b-a4b-it-qat` | 15 GB | 66%/34% | 30.8 | 29.0 | 64 | 25.1s |
| `qwen3.6:27b-mtp-q4_K_M` | 18 GB | 74%/26% | 8.6 | 9.4 | 17 | 11.5s |
| `gemma4:31b-it-qat` | 18 GB | 72%/28% | 3.1 | 3.2 | 14 | 10.8s |

**This measures bases, not the lineup.** Models were called by raw Ollama name,
so there is no repo system prompt and no builder `PARAMS` — these numbers are not
directly comparable to the **Current Benchmark Snapshot**, which times the built
`gemma`/`qwen`/`lite` tags. Read it as a survey of what the hardware does with
each base, not as a leaderboard revision.

Finding: once a model spills, **active parameters per token — not file size —
sets throughput.** `qwen3.6:27b-mtp-q4_K_M` is smaller than the 35B MoE (18 GB vs
22 GB runtime) and spills about the same (74% vs 76% CPU) yet generates 4.6×
slower, because it is dense and every parameter is read per token across DDR4.
`gemma4:31b-it-qat` is the same story at 3.1 tok/s. Both spillover dense models
are batch-only in practice: a 500-token reply costs ~1 min and ~2.7 min
respectively. This is the sharpest evidence yet for the **Hardware** section's
dense-vs-MoE claim, which previously rested on models that also differed in size.

Finding: `gemma4:12b-it-qat` is the only new base worth lineup consideration. At
7.3 GB it lands 98% on GPU and reaches 59.7 tok/s with 374 tok/s prompt ingest —
second only to `lite`, and the fastest non-9B option here. Untested on quality;
speed alone does not earn a slot.

Two limits on these numbers, both load-bearing:

- **The thinking columns show nothing.** `run-speed.py` caps output at
  `num_predict=200` in both modes, so every model emitted exactly 200 tokens and
  wall-clock was equal by construction. Thinking changes how *many* tokens a model
  emits, not how fast it emits them, so this script cannot see its cost. The ON
  column is a consistency check, not evidence that thinking is free. Measuring the
  real cost needs uncapped generation compared on `eval_count` and wall-clock —
  see the open item below.
- **Sub-10% gaps are noise.** n = 2 per model with no warmup. `qwen3.5:9b`
  measured 97.0 tok/s in an earlier run the same day and 90.5 here under identical
  settings; `gemma4:26b-a4b` reading lower ON than OFF (29.0 vs 30.8) is that same
  drift. Rank ordering is far outside the noise; adjacent rows within ~10% are not
  distinguishable.

Open item: no tokens-to-completion benchmark exists. Scope it to the three
non-spillover-dense models — uncapped on `gemma4:31b-it-qat` at 3 tok/s would
exceed the 900s timeout on a normal thinking pass.

### Ollama-era standard pass (2026-07-28)

Full `standard` pass, 2026-07-28, three models, 3 attempts/task, identical
`PARAMS` across all builders and `OLLAMA_MAX_LOADED_MODELS=1` throughout. This is
the first pass on this repo where every suite in the table came from one run of
one lineup on one machine configuration.

Read it with two caveats. Samples are small (n = 9-27 per model), so several rows
carry a tie flag and wide Wilson intervals. And `--seed` does not survive a
process restart on this box (see **Reproducibility**), which is not theoretical
here: `lite`'s persona clean rate read 62% then 57% on two runs of the same suite
against builds differing only by a rule separately measured to change nothing.
Treat failures and the leak-rate gap as strong signal and a few points either way
as noise.

One number in this snapshot is worth reading against its own history. `gemma`
scored 76% on the persona suite under `OLLAMA_MAX_LOADED_MODELS=2` earlier the
same day and 43% twice under `=1`. That is almost certainly not the co-residency
setting — nothing about it should touch rule compliance — but the two readings
straddle a config change, so it is not a clean measurement of anything and is
recorded here as an anomaly to re-check rather than a finding.

#### Speed (`run-speed.py`)

Run: `eval/runs/20260728T215614Z/speed/summary.md`

| Rank | Model | Gen tok/s | Prompt tok/s | Load | Size | GPU/CPU split |
|---|---|---:|---:|---:|---:|---|
| 1 | `lite` | 89.4 | 9562 | 7.3s | 5.9 GB | **100% GPU** |
| 2 | `qwen` | 40.9 | 1563 | 12.5s | 22 GB | 75%/25% CPU/GPU |
| 3 | `gemma` | 28.3 | 6465 | 7.9s | 15 GB | 66%/34% CPU/GPU |

Finding: fitting in VRAM is worth more than any architectural advantage here.
`lite` is 2x `qwen` and 3x `gemma` on generation and leads prompt ingest as well,
which is the number that matters for agentic tools that ship large contexts.
`qwen`'s 1563 tok/s prompt ingest is the weak spot to watch — it is 6x slower than
`lite` at reading input, which shows up as end-to-end latency on the JSON suite
despite comparable generation speed.

This table also quantifies the co-residency effect that forced the rerun: under
`OLLAMA_MAX_LOADED_MODELS=2`, `lite` measured 38.6 tok/s at 18%/82% CPU/GPU. The
model did not change; only what else was resident did.

#### Coding (`run-code.py`)

Run: `eval/runs/20260728T215725Z/code/summary.md`

| Rank | Model | Pass rate | Passed | Avg s | Tok/s |
|---|---|---:|---:|---:|---:|
| 1 | `gemma` | 96% | 26/27 | 6.4 | 31 |
| 2 | `lite` | 85% | 23/27 | 2.3 | 90 |
| 3 | `qwen` | 85% | 23/27 | 4.5 | 51 |

Finding: `gemma` leads, but `lite` matches the 35B `qwen` at a third of the
latency. On a wall-clock basis `lite` is the most productive of the three for
small-function work.

#### Content (`run-content.py`)

Run: `eval/runs/20260728T220323Z/content/summary.md`

| Rank | Model | Clean rate | Clean | Avg s | Tok/s | Avg words |
|---|---|---:|---:|---:|---:|---:|
| 1 | `gemma` | 100% | 9/9 | 10.9 | 30 | 206 |
| 2 | `qwen` | 89% | 8/9 | 11.9 | 30 | 223 |
| 3 | `lite` | 78% | 7/9 | 4.1 | 90 | 212 |

Finding: `gemma` keeps the content crown it held on the retired bases. Format
discipline is where model size still pays.

#### Learning (`run-learn.py`)

Run: `eval/runs/20260728T221541Z/learn/summary.md`

| Rank | Model | Teach /10 | Code pass | Explanation /10 |
|---|---|---:|---:|---:|
| 1 | `qwen` | 9.9 | 12/12 | 9.9 |
| 2 | `gemma` | 9.8 | 12/12 | 9.8 |
| 3 | `lite` | 7.0 | 9/12 | 9.4 |

Finding: a tie at the top (0.1 apart, inside the threshold). `lite`'s gap is
entirely the code gate, not teaching quality — its explanations score 9.4 when the
code runs. Note the ceiling: every model sits at 9.4-9.9 under the default rubric,
which is a rubric that has stopped discriminating. Rerun with `--judge-rubric
strict` before treating this suite as a real ranking.

#### Tutor (`run-tutor.py`, leak-gated)

Run: `eval/runs/20260728T223432Z/tutor/summary.md`

| Rank | Model | Teach /10 | Leaks | Explanation /10 |
|---|---|---:|---:|---:|
| 1 | `gemma` | 9.5 | **0/15** | 9.5 |
| 2 | `qwen` | 5.6 | 6/15 | 9.1 |
| 3 | `lite` | 5.3 | 6/15 | 8.9 |

Finding: the one decisive result in the whole pass, and the only suite where the
gap is far outside noise. All three models explain well (8.9-9.5); only `gemma`
can be asked for help without handing over the answer. The leak gate is
deterministic execution, not judge opinion, which makes this the most trustworthy
number in the snapshot.

#### JSON / long-context (`run-json.py`)

Run: `eval/runs/20260728T221011Z/json/summary.md`

| Rank | Model | Score | Schema OK | Fact rate | Avg s | Tok/s |
|---|---|---:|---:|---:|---:|---:|
| 1 | `lite` | 100% | 100% | 100% | 1.9 | 83 |
| 2 | `qwen` | 100% | 100% | 100% | 7.2 | 42 |
| 3 | `gemma` | 100% | 100% | 100% | 6.0 | 29 |

Finding: schema-constrained decode is solved for all three at this context size —
a three-way 100%, so the suite no longer discriminates on correctness and breaks
purely on latency, where `lite` is ~3x faster. To make this suite informative
again, raise `--context-pressure`.

#### Prompt stack (`run-persona.py`)

Run: `eval/runs/20260728T220725Z/persona/summary.md` (stacked),
`eval/runs/20260728T230429Z/persona/summary.md` (baseline)

| Rank | Model | Clean rate | Clean |
|---|---|---:|---:|
| 1 | `qwen` | 95% | 20/21 |
| 2 | `lite` | 62% | 13/21 |
| 3 | `gemma` | 43% | 9/21 |

Finding: the model that wins the most quality suites is the worst at obeying the
stack, and the ranking here is uncorrelated with every other suite. `gemma` fails
`model_origin`, `unknown_fact`, `unverified`, and `fields_echo`; `qwen` holds all
but `familiar_skill`. If the prompt stack's rules matter for a given use, that is
an argument for `qwen` regardless of who wins coding.

Per-rule stacked-vs-baseline contribution is in **Measuring what the prompt stack
contributes** above; the 2026-07-28 result is that only `bash_block` was free, and
it was deleted.

#### Ollama-era picks (2026-07-28)

From the 2026-07-28 `standard` pass above.

| Use | Pick | Basis |
|---|---|---|
| Fast local default | `lite` | 89 tok/s at 100% GPU — 2× `qwen`, 3× `gemma`, no CPU spill. |
| Agentic tools (Cline) | `lite` | Prompt ingest 9562 tok/s vs `gemma` 6465, `qwen` 1563. |
| Coding puzzles / small functions | `gemma` | 26/27 vs 23/27 for both others; `lite` matches `qwen` at a third of the latency. |
| Content / SEO / copy | `gemma` | 9/9 clean vs `qwen` 8/9, `lite` 7/9. |
| Leak-gated tutoring | `gemma` | **0/15 leaks** vs 6/15 for both others; teach 9.5 vs 5.6/5.3. The one decisive gap in the pass, and deterministic rather than judge-scored. |
| Learning explanations | `qwen` / `gemma` (tie) | 9.9 vs 9.8, inside the threshold; both 12/12 on the code gate. Rubric is saturated — rerun `--judge-rubric strict` before trusting the order. |
| Structured JSON / consumer-app smoke tests | `lite` | Three-way 100%; `lite` ~3× faster (1.9s vs 6.0/7.2s). |
| Prompt-stack fidelity | `qwen` | 95% clean vs `lite` 62%, `gemma` 43%. Uncorrelated with every other suite. |

There is no overall winner, and the split is the useful result: `gemma` takes the
quality suites, `lite` takes everything speed-shaped while staying competitive on
quality, and `qwen` is the only model that reliably obeys the prompt stack. The
surprise is `lite` — a dense 9B taking three rows purely because it fits in VRAM.

### Lineup revert, third model, and measurement hardening (2026-07-28)

Four things changed, in the order they unblocked each other.

**`build-qwen` reverted to the official base.** The 2026-07-27 retarget pointed at
`hf.co/HauhauCS/Qwen3.6-35B-A3B-Uncensored-HauhauCS-Aggressive:Q4_K_M`, which was
never pulled — so `build-qwen` had been failing its own preflight and the `qwen`
tag did not exist. `qwen3.6:35b-a3b-mtp-q4_K_M`, which the docs described as "no
longer installed", was in fact installed. Reverted to it. That also settles the
`prompts/safety.md` contradiction without a benchmark: a base tuned to refuse
nothing, running under a stack whose job is operational caution, means every turn
pays tokens for a fight, and `run-persona.py` would be scoring that fight rather
than the stack. An uncensored base is a separate tag with a separate stack, not a
swap under the shared one.

**`lite` (`qwen3.5:9b`) added as a third model.** It is the only model in the
lineup that fits entirely in 10 GB, so it anchors the leaderboard with a
no-spillover control — without it, every speed number is a measurement of CPU
spill and there is nothing to compare that cost against. It also fixes a
measurement dead end the previous entry flagged as an open item: leave-one-out
grading over a 2-model lineup leaves exactly one judge per response, so
inter-judge disagreement printed `n/a` and could never print anything else. Three
models means two judges per response.

**Judge scoring got a median.** `_judge.judge_total()` scores each response
`--judge-repeats` times (default 3) per judge and takes the median, then averages
across judges. Median rather than mean because a judge that misfires misfires
hard — a 2 read as a 0 — and a mean lets one bad sample drag the score. The two
noise axes are now separated in the summaries: *within-judge* spread (absorbed by
the median) and *between-judge* σ (the panel). A failed parse returns `None`, not
0, so a judge returning garbage is missing data rather than a penalty against the
model it was grading.

**A dead server can no longer masquerade as a failing model.** Mid-session,
`systemctl restart ollama` landed during a run. Every runner caught the
connection errors per-attempt, recorded each as a failure, ran to completion,
exited 0, and wrote summaries reporting that all three models scored 0.0/10 —
authoritative-looking artifacts describing nothing but a closed socket. Three
suites were discarded. `_ollama.py` now has `preflight()` (abort before creating
a run dir if the server is down or a model tag is missing, mirroring
`build-common.sh`'s base-model check) and `check_alive()` (abort after
`DEAD_SERVER_STREAK` consecutive connection failures when the server confirms
down). Standing lesson, and the same shape as the `Unverified:` contamination
lesson below: **a runner that cannot distinguish "the model failed" from "nothing
was listening" will happily produce a confident number for neither.**

Two operational notes from the same session. `OLLAMA_MAX_LOADED_MODELS` moved
2 → 1: at 2, resident models compete for the same 10 GB and a model's measured
throughput depends on which other model happens to be loaded beside it, which is
co-residency, not model quality. Measured directly — `lite` ran 38.6 tok/s
alongside a second resident model and 93 tok/s with the card to itself. Any pass
that straddles that change is mixing machine states and has to be rerun, which is
the same class of error as the 2026-06-14 sampler confound. And the rebuild-then-
verify rule is now `make check` plus an optional pre-commit hook rather than a
line of documentation, with `eval/promote.py` regenerating the README leaderboard
from `eval/runs/` so the hand-maintained tables cannot drift again.

#### What the judge changes actually bought — a partial negative result

The third judge delivered: inter-judge disagreement printed a real number for the
first time (σ 0.17/10 on learn across 36 responses, σ 0.36/10 on tutor across 45),
where a 2-model lineup could only ever print `n/a`. Parse rates were 100% on every
judge except one `lite` call out of 90.

The median did not. Within-judge spread averaged **0.14/10 on learn and 0.34/10 on
tutor**, with 0 of 72 and 2 of 90 judge×response medians varying by ≥2 points
between repeats. `--judge-repeats 3` tripled judging time — the dominant cost in a
`standard` pass — to absorb almost nothing.

The honest reading is that this is not yet evidence the repeats are useless, because
the measurement is confounded by a ceiling: under the default rubric every model
scored 8.9-9.9/10, and a metric pinned to its maximum cannot show variance. The
right next step is to rerun with `--judge-rubric strict` and re-measure the spread
where the scores have room to move. If it stays near zero there, drop the default
to 1 and keep the flag for rubric changes. **Do not conclude the repeats are free
insurance from a run where nothing could have moved.**

#### Per-rule stack contribution, and one deletion

`run-persona.py` stacked vs `--system-mode baseline`, 3 attempts × 3 models, so
each rule scores out of 9:

| Rule | Stacked | Baseline | Buys | Read |
|---|---:|---:|---:|---|
| `identity` | 9/9 | 0/9 | +9 | load-bearing |
| `model_origin` | 7/9 | 0/9 | +7 | load-bearing |
| `familiar_skill` | 5/9 | 1/9 | +4 | load-bearing but leaky — no model holds it 3/3 |
| `unknown_fact` | 6/9 | 3/9 | +3 | load-bearing on `qwen`/`lite`; `gemma`'s clean baseline is the known artifact |
| `unverified` | 3/9 | 0/9 | +3 | `qwen` only; 0/3 on `gemma` and `lite` stacked *and* unstacked |
| `fields_echo` | 3/9 | 0/9 | +3 | `qwen` only, same shape |
| `bash_block` | 9/9 | 9/9 | **0** | free — deleted |

Only `bash_block` was cut. It is the sole rule measured at zero contribution
twice, on two different lineups (2 models on 2026-07-27, 3 models here) — all
bases fence commands in `bash` without a `$` prefix unprompted. The deletion was
verified through `make check`: `bash_block` still scores 3/3 on all three models
with the rule gone. The persona task was kept as a regression guard on that
assumption rather than deleted with the rule.

`unverified` and `fields_echo` were **not** cut despite failing on two of three
models. They are not free — deleting them would cost `qwen` a rule it does obey —
so they are rewrite candidates, not cut candidates, and the previous entry's
lesson applies to any rewrite: the eval task must not share its subject with an
example in the stack.

The uncomfortable result is the ranking itself. `gemma` wins coding, content, and
tutoring while scoring **43% on the prompt stack**; `qwen` wins the stack at 95%
while losing most quality suites. Prompt-stack fidelity is uncorrelated with
capability here, so "which model is best" genuinely depends on whether the rules
in `prompts/` matter for the task at hand.

### Prompt-stack baseline and the `Unverified:` experiment (2026-07-27)

First run of `run-persona.py`, 5 attempts × 7 rules × 2 models, stacked vs
`--system-mode baseline`.

Overall the stack is doing real work: `gemma` 31% → 66%, `qwen` 14% → 80%. Per-rule
contribution (stacked minus baseline, out of 10 across both models):

| Rule | Contribution | Read |
|---|---:|---|
| `identity` | +9 | load-bearing |
| `model_origin` | +8 | load-bearing |
| `familiar_skill` | +8 | load-bearing |
| `unknown_fact` | +5 | load-bearing (all of it from `qwen`) |
| `fields_echo` | +5 | works on `qwen` (5/5), dead on `gemma` (0/5) |
| `bash_block` | 0 | already free — both models comply unprompted |
| `unverified` | 0 | broken — 0/10 stacked *and* unstacked |

The sharpest single result: unstacked, `qwen` answered the `unknown_fact` probe —
compensation on a contract the profile names but attaches no figure to — with
**"$12,000"** plus a fabricated legal citation. `memory/user.md` is what prevents
that. Note that `gemma`'s clean baseline on that task is an artifact — with no
profile it has never heard of the operator, so it declines for the wrong reason.

#### The `Unverified:` rewrite — a negative result

`prompts/system.md` was rewritten to replace two abstract bullets with a trigger
list plus a worked example (`OLLAMA_MAX_QUEUE` / `ollama serve --help`). Measured
against the then-current persona task, the rule went **0/10 → 10/10**.

On 2026-10-09 the example became `--cache-reuse` / `llama-server --help`, since
Ollama is gone from the box. The `unverified` task asks about a git config key,
so the new example stays disjoint from it. Persona runs before and after that date
used different stacks. A check on the rebuilt stack (3 attempts per task, scratch
router) gave the same totals as the published run: `qwen` 21/21, `lite` and
`gemma` 12/21, with `unverified` 3/3, 0/3 and 0/3.

That number was wrong, and the way it was wrong is worth remembering. The persona
task happened to ask about the same Ollama queue variable the new example used, so
it measured whether a model can copy an example, not whether it learned a rule.
Two follow-up checks:

- Four unrelated exact-fact probes (systemd path, pacman flag, default port, git
  config key): marker present in only **5/8**.
- The persona task rewritten to a different subject (git `init.defaultBranch`) and
  re-run: **0/10 again** — identical to before the rewrite.

So the rewrite buys compliance when the question resembles the example or the model
is genuinely unsure, and buys nothing when the model is confident and the user
presses for specifics. In the failing runs `qwen` also invented a
`GIT_DEFAULT_BRANCH` environment variable — the exact failure the rule exists to
stop.

Standing lessons: **an eval task must not share its subject with an example in the
prompt stack**, or the suite grades mimicry; and a large jump on a single task is a
reason to go looking for contamination, not a reason to celebrate.

### Lineup rebuild and confound fix (2026-07-27)

An audit found the repo non-functional: both documented bases had been removed
from Ollama, so `build-gemma` and `build-qwen` failed at `ollama create` and every
runner would `GEN-FAIL` on every attempt. `models/*/system.txt` still carried
`Built: 2026-06-14`, which made a broken state look like a good build.

Changes:

- `gemma` retargeted to `gemma4:26b-a4b-it-qat`; `qwen` to
  `hf.co/HauhauCS/Qwen3.6-35B-A3B-Uncensored-HauhauCS-Aggressive:Q4_K_M`. Both are
  MoE, so neither fitting in 10 GB of VRAM is tolerable.
- **`PARAMS` made identical across both builders**, removing the sampler confound
  described in the snapshot warning. Both now run `num_ctx 65536`, `temperature
  0.2`, `top_p 0.95`, `top_k 40`, `min_p 0.05`, `presence_penalty 0.0`,
  `repeat_penalty 1.05`.
- `num_ctx` raised 16384 → 32768, resolving a drift where the docs claimed 32K
  while the builders shipped 16K and `run-json.py` pinned 65536 per call.
- `build-common.sh` gained a base-model preflight: builders now abort with the
  installed-model list if `BASE_MODEL` is not pulled, instead of writing a
  half-complete `system.txt` and dying later.

- `run_program()` in `eval/_ollama.py` now confines model-generated Python with
  bubblewrap (see **Safety**), closing the un-sandboxed-execution caveat that had
  stood since the exec runners were written. Prompted by the new `qwen` being a
  newly-pulled community tune.

A `smoke` pass on the new lineup came back clean — 4/4 coding, 2/2 content,
2/2 JSON, both models — confirming the harness works end to end against both
rebuilt tags. Speed is the one regression worth noting: generation dropped to
~31.5 tok/s for both models (the retired `gemma` ran 54 tok/s fully on GPU),
because neither new base fits in 10 GB of VRAM. `gemma` keeps a ~4× prompt-ingest
edge (6449 vs 1633 tok/s), which is the number that matters for agentic tools.

Open items from the same audit, in priority order: no suite tests the prompt stack
itself (identity, `Unverified:` prefixing, and the `memory/user.md` honesty rules
are all unenforced); the judge panel still has only two models, so leave-one-out
leaves a single judge and inter-judge disagreement stays `n/a`; no runner accepts
`--seed`, so nothing is reproducible; results are Markdown-only with `eval/runs/`
gitignored, so there is no machine-readable history and the leaderboards are
hand-maintained (which is how the drift above went unnoticed).

> **Status as of 2026-07-28.** Prompt-stack suite: closed by `run-persona.py`.
> Two-model judge panel: closed by adding `lite`. `--seed`: added, but only
> partially closed — it pins sampling within a process and does not survive a
> restart (see **Reproducibility**). Hand-maintained leaderboards: closed by
> `eval/promote.py`. Still open: results remain Markdown-only, so `promote.py`
> parses summaries rather than reading a machine-readable record, and cannot tell
> which base a run used — its staleness guard compares run dates, which is a
> proxy. A `result.json` per run is the real fix.

### Archived model-selection decision (2026-05-31)

From the 2026-05-29 run, the project first consolidated a larger model field
into purpose-built content/coding/tutor roles:

| Role | Model | Basis |
|---|---|---|
| Content generation | `gemma-content` | 5/5 clean at 180 tok/s. |
| Coding assistant | `granite-coder` | 27/30 pass@1 at 1.7 s/call. |
| Coding tutor | `granite-tutor` | Frontrunner teaching score, final pick deferred. |

Early leaderboards:

| Suite | Winner | Result |
|---|---|---|
| Content | `gemma-content` | 5/5 clean, 180 tok/s. |
| Coding | `granite-coder` | 27/30. |
| Teaching | `granite-coder` | 9.9/10, code 12/12. |
| Speed | `qwen-custom` | 87.8 tok/s, 100% GPU. |

### Quant and context exploration (2026-06-02 to 2026-06-03)

Key outcomes:

- Gemma quant/context sweeps showed sliding-window attention makes high context
  cheap; Gemma stayed fully on GPU at large context in those rounds.
- Dense Qwen3.6 27B spillover was unusable at about 3 tok/s.
- Qwen3.6 MoE spillover was much more viable than dense spillover.
- Granite Q5 was a useful quality bump over Q4 in prior coding tests, but later
  current-lineup testing favored Gemma/Qwen.

### 3x3 role matrix (2026-06-03)

The repo briefly expanded into content/coder/tutor variants across Gemma,
Granite, and Qwen families. Results:

| Role suite | Winner | Result |
|---|---|---|
| Content | `gemma-content` | 4/5 clean, 99 tok/s. |
| Coding | `gemma-coder` | 28/30, 104 tok/s. |
| Tutor | `qwen-tutor:think` | 9.0/10, 1/15 leaks. |

Decision at that point: Gemma for all three roles, mainly because it was fast,
clean, and avoided model reload churn.

### Qwen3.6 MoE promotion (2026-06-03 to 2026-06-06)

The MTP MoE line proved viable despite heavy CPU spill. Earlier speed sweeps put
it around the low-30 tok/s band thinking-off, with later runs showing stronger
coding and tutor behavior. It was promoted into the current `qwen` slot for
reasoning/coding comparison against Gemma.

### Head-to-head update (2026-06-07)

This completed Gemma/Qwen run changed the practical split:

- Gemma remains the fast content model.
- Qwen is now the clear coding and learning model in the local lineup.
- Leak-gated tutor status still needed a current rerun (done 2026-06-09).

### Head-to-head update (2026-06-09)

Full five-suite rerun:

- Speed: Gemma 58.3 tok/s (100% GPU) vs Qwen 40.6 tok/s (75%/25% CPU/GPU).
- Coding: Qwen still 30/30; Gemma improved to 29/30 (`calc` 4/5, up from 2/5).
- Content: both now 5/5 clean; Gemma keeps the pick on speed.
- Learning: Qwen 9.9 vs Gemma 9.4, both code 12/12.
- Tutor (leak-gated, finally rerun): Gemma 8.0/10 with 2/15 leaks beats Qwen
  5.9/10 with 6/15 leaks. Qwen explains better when it does not leak but fails
  the gate more often, so Gemma is the tutoring pick.

### Head-to-head update (2026-06-14)

First full `standard` pass on the expanded task set (coding 9, content 3, JSON 7)
with run artifacts retained under `eval/runs/`:

- Speed: Gemma 54.0 tok/s (100% GPU) vs Qwen 46.7 (74%/26% CPU/GPU); Gemma's
  prompt ingest ~10× Qwen's.
- Coding: now a tie at 26/27 each (was Qwen 30/30 vs Gemma 29/30 on the 6-task
  set). Gemma misses `calc`, Qwen misses `decode_string`.
- Content: Gemma 9/9 vs Qwen 7/9 — Qwen regressed on `tech_explain`.
- JSON: both 100% on 7 tasks; Gemma ~2× faster latency.
- Learning: flipped to Gemma 9.7 vs Qwen 9.2 (within threshold) after Qwen
  dropped a code gate (11/12); Qwen still scores higher on raw explanation.
- Tutor: gap widened — Gemma 6.9/10 with 3/15 leaks vs Qwen 3.8/10 with 9/15.

Net: the coding and learning picks that previously went to Qwen are now ties at
best; Gemma wins or ties every suite and is the better hardware fit. Standing
caveats: small n and a single-judge learn/tutor panel.
