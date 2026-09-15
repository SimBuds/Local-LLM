# Testing

This is the source of truth for how this repo evaluates local models served by
llama.cpp's `llama-server` router (Ollama until 2026-09-14): runner usage, safety
notes, current benchmark results, and historical testing decisions. `README.md`
only carries the operational summary and compact leaderboards.

## Goals

The suite answers four practical questions:

1. **Can the model follow content instructions?** Format discipline, SEO keyword
   control, length, and Markdown structure.
2. **Can the model write correct code?** Pass@1 on small self-contained Python
   tasks with hidden asserts.
3. **Can the model teach without leaking?** Explanation quality, code gate, and
   solution-leak checks for tutor use.
4. **Does the prompt stack actually hold?** Whether the rules in `prompts/`,
   `memory/`, and `knowledge/` are obeyed — identity, honesty about Casey's skill
   buckets, `Unverified:` marking, output shape.

Question 4 is the odd one out and the reason `run-persona.py` exists: suites 1–3
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
(all seven since 2026-09-15, pinned by `eval/test_preflight.py`). `run-json.py`
then also checks each model's served context. Only `run-learn.py`,
`run-persona.py`, and `run-tutor.py` abort mid-run if the server stops answering.
The other four record those attempts as failures. See the 2026-07-28 note in
**Historical Notes** for why preflight matters:
a restart during a run previously produced complete, exit-0 summaries reporting
that every model scored zero.

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
| `run-json.py` | `--tasks ...`, `--num-ctx N` (minimum served context, checked before the run, default 32768), `--context-pressure normal|medium|high`, `--position default|early|middle|late|all`, `--thinking auto|on|off` |
| `run-persona.py` | `--tasks ...`, `--system-mode stacked|baseline`, `--thinking auto|on|off` (defaults off) |

Thinking mode can be forced with `--thinking on`, disabled with `--thinking off`,
or selected per model by appending `:think` to the model spec. Do not use
thinking mode for content runs unless explicitly testing it.

`run-json.py --context-pressure` scales document length to probe true
long-context degradation: `normal` (default) is the standard ~6-7k-token
prompts, `medium` lands ~15-19k, and `high` ~21-27k, as close to the 32k served
context as fits. On 2026-09-14 `high` measured 21250 to 27301 prompt tokens on
`lite` with no context error. A prompt that does overflow gets an HTTP 400 from
llama-server and is recorded as a failed attempt, never silently truncated. `--position early|middle|late|all`
moves the buried needle to measure position bias. Both are manual sweeps, not
part of the default comparison. `--judge-rubric strict` on the learn/tutor
runners pushes judges to reserve top marks when default grading saturates.

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
warning when a response was scored by fewer than two judges — with the current
2-model lineup, leave-one-out always leaves a single judge, so those /10 scores
are soft signal until a third model joins the panel). Tutor ranking breaks
teach-score ties on leak rate: the model that leaks less wins.

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
```

| Profile | When to run | Runtime | What it does |
|---|---|---|---|
| `smoke` | After every `build-*` rebuild or runner change | ~5-10 min | Speed (capped output) + 2 coding tasks + SEO content + 2 persona tasks + 1 JSON task, 2 attempts each |
| `standard` | When picking models or after prompt-stack changes | ~1-2 hours | All seven suites; code/content/persona trimmed to 3 attempts so the expanded task set stays in budget |
| `deep` | Before trusting a close call or promoting a new model | several hours | Full 5-attempt sweeps, both persona system modes, plus medium and high context-pressure JSON runs |

Runtime note: the original "under 1 hour" `standard` budget assumed two models at
54/46.7 tok/s running fully or mostly on GPU. Two things have since inflated it.
Two of the three current models spill to CPU, and — the larger factor — the judged
suites scale with the **square** of the model count: `run-learn.py` and
`run-tutor.py` generate `models x tasks x attempts` responses and then grade each
with `models-1` judges x `--judge-repeats` calls. Going 2 -> 3 models roughly
triples the judging work. Budget ~2 hours for a 3-model `standard`; drop
`--judge-repeats` to 1 to trade the median back for speed.

The wrapper prints every `summary.md` it produced at the end. Individual runners
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

## Current Benchmark Snapshot

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

### Speed (`run-speed.py`)

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

### Coding (`run-code.py`)

Run: `eval/runs/20260915T012204Z/code/summary.md`

| Rank | Model | Pass rate | Passed | Avg s | Tok/s |
|---|---|---:|---:|---:|---:|
| 1 | `gemma` | 100% | 27/27 | 9.3 | 46 |
| 2 | `qwen` | 89% | 24/27 | 9.6 | 75 |
| 3 | `lite` | 81% | 22/27 | 2.3 | 153 |

Finding: `gemma` went 26/27 → 27/27. `qwen` and `lite` both failed every
`decode_string` attempt, and `lite` also missed 2 of 3 `calc` attempts.

### Content (`run-content.py`)

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

### Learning (`run-learn.py`)

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

### Tutor (`run-tutor.py`, leak-gated)

Run: `eval/runs/20260915T021916Z/tutor/summary.md`

| Rank | Model | Teach /10 | Leaks | Explanation /10 |
|---|---|---:|---:|---:|
| 1 | `gemma` | 9.4 | **0/15** | 9.4 |
| 2 | `qwen` | 6.1 | 5/15 | 9.3 |
| 3 | `lite` | 5.0 | 7/15 | 9.0 |

Finding: the decisive result from the Ollama era holds on the new runtime.
`gemma` leaked nothing (Wilson 0 to 20%), while `qwen` (15 to 58%) and `lite`
(25 to 70%) handed over working solutions. Judges parsed 270/270 calls, σ 0.33/10.

### JSON / long-context (`run-json.py`)

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

### Prompt stack (`run-persona.py`)

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

## Current Picks

From the 2026-09-15 llama.cpp `standard` pass above.

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

## Hardware

Benchmarks are for this local machine:

| Component | Value |
|---|---|
| GPU | RTX 3080, 10 GB VRAM |
| CPU | Ryzen 5900x |
| RAM | 32 GB DDR4-3600 |
| Ollama | 0.30-era testing for the 2026-07-28 snapshot, 0.33.3 at the switch |
| llama.cpp | build 10968 (`41abbfd59`), static, CUDA 13.4, from 2026-09-14 |
| Desktop VRAM | about 1 GB held by desktop apps at idle, so about 8.6 GB for model plus KV cache |

Models that fit 100% on GPU are fast. Dense spillover usually collapses
generation speed because DDR4 bandwidth becomes the bottleneck. MoE spillover is
less punishing because only a subset of parameters is active per token.

The **Base-model speed survey (2026-08-11)** isolates this: a dense 27B and a
larger 35B A3B MoE spill nearly identically (74% vs 76% CPU), and the dense one
runs 4.6× slower. Size on disk does not predict throughput once spilling starts —
active parameters per token does.

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
| `gemma4:31b-it-qat` | speed-only | Not in the lineup. Dense 31B, 18 GB, 3.1 tok/s under 72% CPU spill. Slowest base measured here; effectively unusable interactively. |
| `gemma` (`gemma4:12b-it-q4_K_M`) | retired 2026-07-27 | Base no longer installed. Won/tied all six suites on 2026-06-14, but that run was sampler-confounded. |
| `granite` (`granite4.1:8b-Q5_K_M`) | dropped | Strong prior coding runs, but no longer leads the current lineup. |
| `qwen-custom` (`qwen3.5:9b`) | removed | Fast 9B-era thinking model; superseded by current Qwen3.6 MoE results. |
| `ministral-custom` | removed | Strong historical #2; removed after Gemma/Granite consolidation. |
| `llama-custom` | removed | Last or near-last in early content/coding/teaching runs. |
| `qwen-big` / `qwen-moe` experiments | promoted/retired variants | Established that MoE can survive spillover; current `qwen` is the promoted MTP MoE line. |
| `gemma-big` | retired | Larger dense Gemma lost quality/speed tradeoffs on this hardware. |

## Historical Notes

The notes below are retained for decision history. Prefer the current snapshot
above when choosing a model today.

### Same-day Ollama and llama.cpp comparison (2026-09-15)

Earlier speed comparisons set llama.cpp against the 2026-07-28 Ollama tags. This
one ran both runtimes on the same day, on byte-identical weights, with everything
else held equal, to measure what the runtime itself changes.

**Setup.** `gemma` and `qwen` only (`lite`'s weights differ between runtimes).
Ollama 0.33.3 ran the base tags `gemma4:26b-a4b-it-qat` and
`qwen3.6:35b-a3b-mtp-q4_K_M` as the service is configured (q8_0 KV cache, flash
attention, its own GPU/CPU split). Both runtimes got the model's own `prompt.txt`
as the system message, the `run-speed.py` prompts, the same samplers, 32768
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
  tokens against 32768). Nothing is truncated.
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
  described in the snapshot warning. Both now run `num_ctx 32768`, `temperature
  0.2`, `top_p 0.95`, `top_k 40`, `min_p 0.05`, `presence_penalty 0.0`,
  `repeat_penalty 1.05`.
- `num_ctx` raised 16384 → 32768, resolving a drift where the docs claimed 32K
  while the builders shipped 16K and `run-json.py` pinned 32768 per call.
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
