#!/usr/bin/env python3
"""
spike-test: the locked 2 GB margin check for a model's pinned split.

  ./eval/spike-test.py NAME [--cycles 5] [--target-mib 3000] [--preset models/models.ini]

A scratch CUDA process holds enough VRAM to bring other GPU use to about
--target-mib, then a scratch router on a spare port over the repo preset runs
NAME through load, one real request with the full prompt stack (200-token cap)
and unload, --cycles times, sampling GPU use every 0.25 s for the peak. PASS
needs every cycle to answer and no out-of-memory line in the router log. It
prints the rows TESTING.md records and exits 1 on FAIL.

Why it exists: AGENTS.md requires this test for every new pinned split, and it
was written from scratch twice (TESTING.md, *Spike test at 131072 context* and
*Spike test of freedom*). Nothing here touches the live service: it refuses to
start while the service has a model loaded, never uses port 8080, writes only
to a temp folder, and stops everything it started.
"""

from __future__ import annotations

import argparse
import ctypes
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

EVAL = Path(__file__).resolve().parent
REPO = EVAL.parent
sys.path.insert(0, str(REPO / "scripts"))
from _probe import (RouterUnreadable, free_port, read_preset,  # noqa: E402
                    service_busy, stop)

LIVE_URL = "http://localhost:8080"
SAMPLE_S = 0.25
LOAD_TIMEOUT_S = 240
REQUEST_TIMEOUT_S = 300
ROUTER_TIMEOUT_S = 60
MAX_TOKENS = 200
PROMPT = "Explain how a hash map handles collisions, with one short Python example."
# Lower case. Both appeared in the router log when lite failed on 2026-10-31.
OOM_MARKERS = ("out of memory", "cudamalloc failed")


# --- pure parts (unit-tested) -------------------------------------------------

def gpu_memory(smi: str) -> tuple[int, int]:
    """(used, total) MiB from `nvidia-smi --query-gpu=memory.used,memory.total
    --format=csv,noheader,nounits`. One GPU is assumed, as on this box."""
    lines = [line for line in smi.splitlines() if line.strip()]
    if len(lines) != 1:
        raise ValueError(f"expected one GPU line from nvidia-smi, got {len(lines)}")
    used, total = (int(c.strip()) for c in lines[0].split(","))
    return used, total


def hold_size(used: int, target: int) -> int:
    """MiB to hold so other GPU use reaches `target`. Refuses when it already does,
    because the test would then measure the desktop, not the margin."""
    if used >= target:
        raise SystemExit(f"ERROR: other GPU use is already {used} MiB, at or above the "
                         f"{target} MiB target. Close GPU apps or raise --target-mib.")
    return target - used


def oom_lines(log: str) -> list[str]:
    return [line for line in log.splitlines()
            if any(m in line.lower() for m in OOM_MARKERS)]


def table(name: str, cycles: list[dict]) -> str:
    rows = [f"Spike test of `{name}`", "",
            "| Cycle | Other GPU use | Peak | Min free | Load | Generation |",
            "|---:|---:|---:|---:|---:|---:|"]
    for i, c in enumerate(cycles, 1):
        if c.get("error"):
            rows.append(f"| {i} | {c['other']} MiB | failed: {c['error']} | | | |")
        else:
            rows.append(f"| {i} | {c['other']} MiB | {c['peak']} MiB | {c['free']} MiB "
                        f"| {c['load']:.1f} s | {c['tps']:.1f} tok/s |")
    return "\n".join(rows)


def verdict(cycles: list[dict], ooms: list[str]) -> tuple[bool, str]:
    failed = sum(1 for c in cycles if c.get("error"))
    ok = len(cycles) - failed
    if failed or ooms:
        why = f"{ok}/{len(cycles)} cycles answered"
        if ooms:
            why += f", out-of-memory in the router log: {ooms[0].strip()}"
        return False, f"FAIL: {why}"
    if not cycles:
        return False, "FAIL: no cycles ran"
    return True, f"PASS: {ok}/{len(cycles)} cycles, no out-of-memory line"


# --- runtime ------------------------------------------------------------------

def read_gpu() -> tuple[int, int]:
    out = subprocess.run(["nvidia-smi", "--query-gpu=memory.used,memory.total",
                          "--format=csv,noheader,nounits"],
                         capture_output=True, text=True, check=True).stdout
    return gpu_memory(out)


def hold_vram() -> int:
    """Child mode: open a CUDA context and print `ctx`, read the MiB to hold from
    stdin, allocate and touch it, print `held`, then hold until stdin closes. The
    context alone takes about 200 MiB (2026-11-14), so the parent sizes the hold
    after it exists. torch and cupy are not installed, so this is the driver API."""
    cuda = ctypes.CDLL("libcuda.so.1")
    ctx, ptr = ctypes.c_void_p(), ctypes.c_uint64()

    def call(name: str, *args) -> bool:
        rc = getattr(cuda, name)(*args)
        if rc != 0:
            print(f"ERROR: {name} returned CUDA error {rc}", file=sys.stderr, flush=True)
        return rc == 0

    if not (call("cuInit", 0) and call("cuCtxCreate_v2", ctypes.byref(ctx), 0, 0)):
        return 1
    print("ctx", flush=True)
    size = ctypes.c_size_t(int(sys.stdin.readline()) * 1024 * 1024)
    if not (call("cuMemAlloc_v2", ctypes.byref(ptr), size)
            and call("cuMemsetD8_v2", ptr, ctypes.c_ubyte(0), size)):
        return 1
    print("held", flush=True)
    sys.stdin.read()
    return 0


class Sampler(threading.Thread):
    """Peak GPU use, sampled every SAMPLE_S until stopped."""

    def __init__(self):
        super().__init__(daemon=True)
        self.peak = 0
        self.done = threading.Event()

    def run(self):
        while not self.done.is_set():
            try:
                self.peak = max(self.peak, read_gpu()[0])
            except (subprocess.CalledProcessError, ValueError):
                pass
            self.done.wait(SAMPLE_S)


def cycle(gw, name: str) -> dict:
    """One load, request and unload. Returns the row, with `error` on failure."""
    other, total = read_gpu()
    row: dict = {"other": other}
    sampler = Sampler()
    sampler.start()
    try:
        row["load"], _args = gw.load_model(name, LOAD_TIMEOUT_S)
        _text, meta = gw.generate(name, PROMPT, REQUEST_TIMEOUT_S,
                                  options={"num_predict": MAX_TOKENS})
        row["tps"] = gw.tok_per_s(meta)
    except Exception as e:  # noqa: BLE001 - any failure is this cycle's result
        row["error"] = f"{type(e).__name__}: {str(e).splitlines()[0] if str(e) else ''}"
    finally:
        sampler.done.set()
        sampler.join()
        try:
            gw._router_post("/models/unload", name)
            gw._wait_for(name, "unloaded", LOAD_TIMEOUT_S)
        except Exception:  # noqa: BLE001 - a crashed model is already unloaded
            pass
    row["peak"] = sampler.peak
    row["free"] = total - sampler.peak
    return row


def wait_for_router(url: str, proc: subprocess.Popen, log: Path) -> None:
    import urllib.request
    deadline = time.monotonic() + ROUTER_TIMEOUT_S
    while time.monotonic() < deadline:
        if proc.poll() is not None:
            raise SystemExit(f"ERROR: scratch router exited {proc.returncode} (log: {log})")
        try:
            with urllib.request.urlopen(f"{url}/models", timeout=2):
                return
        except OSError:
            time.sleep(0.5)
    raise SystemExit(f"ERROR: scratch router not up within {ROUTER_TIMEOUT_S} s (log: {log})")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[1],
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("name", nargs="?", help="router model name from the preset")
    ap.add_argument("--cycles", type=int, default=5)
    ap.add_argument("--target-mib", type=int, default=3000,
                    help="other GPU use to reach with the hold (default 3000)")
    ap.add_argument("--preset", type=Path, default=REPO / "models" / "models.ini")
    ap.add_argument("--hold", action="store_true", help=argparse.SUPPRESS)
    args = ap.parse_args()

    if args.hold:
        return hold_vram()
    if not args.name:
        ap.error("NAME is required")
    models = read_preset(args.preset)
    if args.name not in models:
        raise SystemExit(f"ERROR: {args.name!r} is not in {args.preset}. "
                         f"Known: {', '.join(sorted(models))}")
    try:
        busy = service_busy(LIVE_URL)
    except RouterUnreadable as e:
        raise SystemExit(f"ERROR: {e}")
    if busy:
        raise SystemExit(f"ERROR: the live service holds {', '.join(busy)}. "
                         f"Wait until it is idle, then rerun.")
    binary = shutil.which("llama-server")
    if not binary:
        raise SystemExit("ERROR: llama-server is not on PATH")

    hold_size(read_gpu()[0], args.target_mib)  # refuse before starting anything
    out = Path(tempfile.mkdtemp(prefix="spike-test-"))
    port = free_port()
    url = f"http://localhost:{port}"
    os.environ["LLM_URL"] = url
    import _gateway as gw  # reads LLM_URL at import, so after it is set

    holder = router = None
    try:
        holder = subprocess.Popen([sys.executable, __file__, "--hold"],
                                  stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)
        if holder.stdout.readline().strip() != "ctx":
            raise SystemExit("ERROR: the VRAM hold could not open a CUDA context")
        hold = hold_size(read_gpu()[0], args.target_mib)
        holder.stdin.write(f"{hold}\n")
        holder.stdin.flush()
        if holder.stdout.readline().strip() != "held":
            raise SystemExit(f"ERROR: the VRAM hold of {hold} MiB failed")
        router_log = out / "router.log"
        with open(router_log, "w", encoding="utf-8") as log:
            router = subprocess.Popen([binary, "--models-preset", str(args.preset),
                                       "--models-max", "1", "--port", str(port)],
                                      stdout=log, stderr=subprocess.STDOUT)
        wait_for_router(url, router, router_log)
        print(f"holding {hold} MiB, router on {port}, log {router_log}", flush=True)
        cycles = []
        for i in range(args.cycles):
            cycles.append(cycle(gw, args.name))
            print(f"cycle {i + 1}/{args.cycles} done", flush=True)
    finally:
        if router is not None:
            stop(router)
        if holder is not None:
            holder.stdin.close()
            stop(holder)

    ok, line = verdict(cycles, oom_lines(router_log.read_text(errors="replace")))
    print(table(args.name, cycles))
    print(line)
    if ok:
        shutil.rmtree(out)
        return 0
    print(f"router log kept: {router_log}")
    return 1


if __name__ == "__main__":
    sys.exit(main())
