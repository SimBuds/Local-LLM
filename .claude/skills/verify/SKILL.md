---
name: verify
description: How to verify a change in this repo at its real surface (the eval runner CLIs against a llama-server router), without disturbing the live service other apps use.
---

# Verifying changes in Local-LLM

The surfaces are the CLIs in `eval/` and the llama-server router they call.
There is no GUI. Unit tests are CI's job, not verification.

## Before driving anything

- The `llama-server` user service on port 8080 is live and shared with other
  apps (Jobhunt, SEO-LLM, editors). Check it first:
  `curl -s localhost:8080/models | jq -c '[.data[]|{id,s:.status.value}]'`
- If another app is using it, ask before sending load. It has one slot, so your
  requests queue behind theirs and your timings are inflated.
- Asking for a model that is not loaded evicts the loaded one. Prefer the model
  already `loaded`, or use a scratch router (below).
- Check for GPU faults on this boot: `journalctl -k -b | grep -i xid`.
  Intermittent Xid 31 crashes are a known open issue (see TESTING.md).

## Drive it

- Always pass `--out-root` to a scratch directory. The newest run in
  `eval/runs/` becomes the README leaderboard.
- One runner, one model, one attempt is usually enough:
  `./eval/run-tools.py --models qwen --attempts 1 --out-root "$SCRATCH/v"`
- Per-attempt artifacts land in `<out-root>/<UTC>/<suite>/<model>/`. Read them:
  that is where the calls, answers and failure reasons are.
- To test an undeployed preset change, use a scratch router:
  `llama-server --models-preset models/models.ini --models-max 1 --port 8081`
  and `LLM_URL=http://localhost:8081`. It shares the GPU with the service, so
  only do this when the service has nothing loaded. Stop it afterwards.

## Probes that need no router load

- Unknown task name: every runner exits 1 naming the valid tasks, no run dir.
- Router down: `LLM_URL=http://localhost:9 ./eval/run-<suite>.py --models qwen`
  must exit 1 with `no llama-server router` and create no run dir.
- Profiles: `./eval/run-profile.py standard --models gemma qwen lite --dry-run`
- Leaderboard: `./eval/promote.py --check` exits 0 when README matches runs.

## Never

- Restart, stop, or deploy the service. That is Casey's (AGENTS.md tier 0).
