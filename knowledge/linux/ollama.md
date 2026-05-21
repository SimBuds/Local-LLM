# Ollama runtime reference (Casey's machine)

Authoritative facts about how Ollama is configured here. Use these verbatim;
do not pattern-match from training data.

## Thinking-mode flags (Qwen3.5 family)

- Disable reasoning for one run: `ollama run --think false <model>`
  (the flag is **`--think false`**, NOT `--nothink` and NOT `--no-think`).
- Enable reasoning but hide the trace: `ollama run --hidethinking <model>`.
- Reasoning is **disabled by default** on Qwen3.5 sizes 0.8B/2B/4B/9B.

## Shell aliases

- `qc` → `ollama run --think false qwen-custom` (default — no thinking).
- `qct` → `ollama run qwen-custom` (thinking on — for code/architecture).

## Systemd environment (set via `sudo systemctl edit ollama.service`)

| Variable | Value | Purpose |
|---|---|---|
| `OLLAMA_KV_CACHE_TYPE` | `q5_0` | Quantized KV cache, ~30% VRAM savings |
| `OLLAMA_FLASH_ATTENTION` | `1` | Required for quantized KV cache |
| `OLLAMA_NUM_PARALLEL` | `1` | Single concurrent request |
| `OLLAMA_CONTEXT_LENGTH` | `16384` | Sole source of truth — do NOT pass `num_ctx` per-call |
| `OLLAMA_KEEP_ALIVE` | `10m` | Idle-unload after 10 min (Ollama stock default is 5m; this machine is 10m) |
| `OLLAMA_MAX_LOADED_MODELS` | `1` | One model in VRAM at a time |

Per-call payload override: `"keep_alive": -1` pins the model during active
work (jobhunt gateway uses this).

## `qwen-custom` Modelfile parameters

Set in `~/ai/build.sh`:

- `temperature 0.6` (stock Qwen3.5:9b is `1`; lowered for technical assistant)
- `top_p 0.95` (stock)
- `top_k 20` (stock)
- `min_p 0`
- `repeat_last_n 256`
- `presence_penalty 1.5` (stock; anti-repetition)

`num_ctx` is intentionally absent — controlled at the server via
`OLLAMA_CONTEXT_LENGTH`.

## Hardware budget

RTX 3080, 10 GB VRAM total. Arch idles ~1.5 GB; `qwen3.5:9b` resident is
~9.1 GB. `OLLAMA_GPU_OVERHEAD` is intentionally unset.
