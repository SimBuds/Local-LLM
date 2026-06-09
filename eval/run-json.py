#!/usr/bin/env python3
"""
JSON / long-context benchmark: the eval the consumer apps (jobhunt, seo-cli)
actually depend on and the other runners ignore. For each task it constrains the
model's decode with a JSON schema (Ollama `format=`), buries the answer facts
inside several thousand tokens of boilerplate, then measures three things:

  1. valid_json   — response parses as JSON at all.
  2. schema_ok    — parsed output conforms to the required schema (keys, types,
                    enums, nullability).
  3. facts        — fraction of content-correctness checks passed: did the model
                    pull the *right* facts out of the long document?

Ranking metric is the composite "task score" = mean over attempts of
(schema_ok AND all-facts-correct). Schema-valid but wrong-fact output does not
count — that's the failure mode that silently ships bad data in the real apps.

Usage:
  ./eval/run-json.py --models qwen gemma
  ./eval/run-json.py --models gemma --tasks jd_extract needle_recall
  ./eval/run-json.py --models qwen gemma --num-ctx 16384 --attempts 5

num_ctx defaults to 32768 to mirror jobhunt's gateway pin (without it Ollama
truncates these long prompts to 4096 and the model answers from a fragment).
Temperature is forced to 0 for deterministic extraction.

Output:
  eval/runs/<UTC>/json/
    summary.md
    <model>/<task>-attempt-<n>.json   (raw model output)
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.error
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _ollama import (  # noqa: E402
    REPO_ROOT, generate, get_effective_think, new_run_dir, resolve_model, tok_per_s,
)
from json_tasks import TASKS, JsonTask  # noqa: E402

DEFAULT_OUT_ROOT = REPO_ROOT / "eval" / "runs"


# --- minimal JSON-schema validator (stdlib only) ------------------------------
def schema_errors(value, schema, path="$") -> list[str]:
    """Validate `value` against the subset of JSON schema the tasks use:
    object/array/string/integer/number/boolean, type-unions incl. "null",
    required, properties, items, enum. Returns a list of error strings."""
    errs: list[str] = []
    t = schema.get("type")
    types = t if isinstance(t, list) else [t]

    if value is None:
        if "null" in types:
            return errs
        return [f"{path}: null not allowed"]

    # pick the first non-null type to validate against
    concrete = next((x for x in types if x != "null"), None)

    if concrete == "object":
        if not isinstance(value, dict):
            return [f"{path}: expected object, got {type(value).__name__}"]
        for req in schema.get("required", []):
            if req not in value:
                errs.append(f"{path}.{req}: missing required")
        for k, sub in schema.get("properties", {}).items():
            if k in value:
                errs += schema_errors(value[k], sub, f"{path}.{k}")
    elif concrete == "array":
        if not isinstance(value, list):
            return [f"{path}: expected array, got {type(value).__name__}"]
        items = schema.get("items")
        if items:
            for i, v in enumerate(value):
                errs += schema_errors(v, items, f"{path}[{i}]")
    elif concrete == "string":
        if not isinstance(value, str):
            errs.append(f"{path}: expected string, got {type(value).__name__}")
    elif concrete == "integer":
        if not isinstance(value, int) or isinstance(value, bool):
            errs.append(f"{path}: expected integer, got {type(value).__name__}")
    elif concrete == "number":
        if not isinstance(value, (int, float)) or isinstance(value, bool):
            errs.append(f"{path}: expected number, got {type(value).__name__}")
    elif concrete == "boolean":
        if not isinstance(value, bool):
            errs.append(f"{path}: expected boolean, got {type(value).__name__}")

    if "enum" in schema and value not in schema["enum"]:
        errs.append(f"{path}: {value!r} not in enum {schema['enum']}")
    return errs


def build_prompt(task: JsonTask) -> str:
    return (
        f"{task.instruction}\n\n"
        f"Respond with a single JSON object matching the required schema. "
        f"Output only JSON.\n\n"
        f"--- DOCUMENT START ---\n{task.context}\n--- DOCUMENT END ---"
    )


def run_attempt(model: str, task: JsonTask, n: int, total: int, timeout: int,
                num_ctx: int, thinking_mode: str) -> dict:
    print(f"    {task.key:14s} [{n}/{total}] ", end="", flush=True)
    name, model_think = resolve_model(model)
    think = get_effective_think(thinking_mode, model_think)
    prompt = build_prompt(task)
    options = {"num_ctx": num_ctx, "temperature": 0.0}

    t0 = time.monotonic()
    try:
        text, meta = generate(name, prompt, timeout, think=think,
                              options=options, fmt=task.schema)
    except (urllib.error.URLError, TimeoutError) as e:
        print(f"FAIL ({time.monotonic()-t0:.1f}s): {e}")
        return {"ok": False, "error": str(e), "elapsed_s": time.monotonic() - t0}
    elapsed = time.monotonic() - t0

    valid_json = True
    parsed = None
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        valid_json = False

    serrs = schema_errors(parsed, task.schema) if valid_json else ["no JSON"]
    schema_ok = valid_json and not serrs

    check_results = []
    if valid_json:
        for label, fn in task.checks:
            try:
                check_results.append((label, bool(fn(parsed))))
            except Exception:
                check_results.append((label, False))
    n_pass = sum(1 for _, ok in check_results)
    n_checks = len(task.checks)
    facts_ok = schema_ok and n_pass == n_checks

    tag = ("ok " if facts_ok else
           "SCHEMA?" if not schema_ok else
           f"facts {n_pass}/{n_checks}")
    print(f"{tag:>9}  {elapsed:6.1f}s  {tok_per_s(meta):5.1f} tok/s  "
          f"prompt~{meta.get('prompt_eval_count', 0)}tok")
    return {
        "ok": True, "valid_json": valid_json, "schema_ok": schema_ok,
        "schema_errors": serrs, "n_pass": n_pass, "n_checks": n_checks,
        "facts_ok": facts_ok, "checks": check_results,
        "elapsed_s": elapsed, "tok_per_s": tok_per_s(meta),
        "prompt_tokens": meta.get("prompt_eval_count", 0), "text": text,
    }


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--models", nargs="+", required=True)
    ap.add_argument("--tasks", nargs="+", default=list(TASKS),
                    help=f"Subset of: {', '.join(TASKS)}")
    ap.add_argument("--attempts", type=int, default=3)
    ap.add_argument("--num-ctx", type=int, default=32768)
    ap.add_argument("--timeout", type=int, default=300)
    ap.add_argument("--thinking", choices=["auto", "on", "off"], default="off")
    ap.add_argument("--out-root", type=Path, default=DEFAULT_OUT_ROOT)
    args = ap.parse_args()

    unknown = [t for t in args.tasks if t not in TASKS]
    if unknown:
        print(f"unknown tasks: {unknown}; choose from {list(TASKS)}", file=sys.stderr)
        return 1
    tasks = [TASKS[t] for t in args.tasks]

    run_dir = new_run_dir(args.out_root) / "json"
    run_dir.mkdir(parents=True)
    print(f"Run dir:  {run_dir.relative_to(REPO_ROOT)}")
    print(f"Tasks:    {', '.join(args.tasks)}  ({args.attempts}/model each)")
    print(f"Models:   {', '.join(args.models)}")
    print(f"num_ctx:  {args.num_ctx}, temperature 0\n")

    summary: dict[str, dict[str, list[dict]]] = {}
    for model in args.models:
        print(f"=== {model} ===")
        mdir = run_dir / model
        mdir.mkdir()
        summary[model] = {}
        for task in tasks:
            rs = []
            for n in range(1, args.attempts + 1):
                r = run_attempt(model, task, n, args.attempts, args.timeout,
                                args.num_ctx, args.thinking)
                if r.get("ok"):
                    (mdir / f"{task.key}-attempt-{n}.json").write_text(
                        r["text"], encoding="utf-8")
                rs.append(r)
            summary[model][task.key] = rs
        print()

    write_summary(run_dir, summary, args, tasks)
    return 0


def write_summary(run_dir, summary, args, tasks) -> None:
    ranked = []
    for model, by_task in summary.items():
        all_r = [r for rs in by_task.values() for r in rs]
        ok = [r for r in all_r if r.get("ok")]
        if not ok:
            ranked.append({"model": model, "score": -1.0, "n_ok": 0,
                           "total": len(all_r)})
            continue
        n = len(ok)
        ranked.append({
            "model": model,
            "score": sum(1 for r in ok if r["facts_ok"]) / n,
            "json_rate": sum(1 for r in ok if r["valid_json"]) / n,
            "schema_rate": sum(1 for r in ok if r["schema_ok"]) / n,
            "fact_rate": sum(r["n_pass"] for r in ok) / sum(r["n_checks"] for r in ok),
            "avg_s": sum(r["elapsed_s"] for r in ok) / n,
            "avg_tps": sum(r["tok_per_s"] for r in ok) / n,
            "n_ok": n, "total": len(all_r), "by_task": by_task,
        })
    ranked.sort(key=lambda r: (-r["score"], -r.get("schema_rate", 0), -r.get("avg_tps", 0)))

    L = ["# JSON / long-context benchmark", "",
         f"- Tasks: {len(tasks)} ({', '.join(t.key for t in tasks)})",
         f"- Attempts per task: {args.attempts}",
         f"- num_ctx: {args.num_ctx} (mirrors jobhunt's gateway pin), temperature 0",
         "- **Score** = fraction of attempts that are schema-valid AND pass every "
         "content check (right facts out of a long document). Schema-valid but "
         "wrong-fact output scores 0.", ""]
    best = next((r for r in ranked if r["score"] >= 0), None)
    if best:
        L += [f"## 🏆 Winner: `{best['model']}` — score "
              f"{best['score']*100:.0f}% (schema {best['schema_rate']*100:.0f}%, "
              f"facts {best['fact_rate']*100:.0f}%)", ""]
    L += ["| Rank | Model | Score | Valid JSON | Schema OK | Fact rate | Avg s | Tok/s |",
          "|---|---|---:|---:|---:|---:|---:|---:|"]
    for i, r in enumerate(ranked, 1):
        if r["n_ok"] == 0:
            L.append(f"| {i} | `{r['model']}` | (all failed) | — | — | — | — | — |")
            continue
        L.append(f"| {i} | `{r['model']}` | {r['score']*100:.0f}% | "
                 f"{r['json_rate']*100:.0f}% | {r['schema_rate']*100:.0f}% | "
                 f"{r['fact_rate']*100:.0f}% | {r['avg_s']:.1f} | {r['avg_tps']:.0f} |")

    # Per-task breakdown (score per model × task)
    L += ["", "### Per-task score (schema-valid AND all facts correct)", "",
          "| Model | " + " | ".join(t.key for t in tasks) + " |",
          "|---|" + "---|" * len(tasks)]
    for r in ranked:
        if r["n_ok"] == 0:
            continue
        cells = []
        for t in tasks:
            rs = [x for x in r["by_task"][t.key] if x.get("ok")]
            passed = sum(1 for x in rs if x["facts_ok"])
            cells.append(f"{passed}/{len(rs)}" if rs else "—")
        L.append(f"| `{r['model']}` | " + " | ".join(cells) + " |")

    (run_dir / "summary.md").write_text("\n".join(L) + "\n", encoding="utf-8")
    print(f"Summary: {(run_dir / 'summary.md').relative_to(REPO_ROOT)}")
    if best:
        print(f"Winner:  {best['model']} (score {best['score']*100:.0f}%)")


if __name__ == "__main__":
    sys.exit(main())
