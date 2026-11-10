"""
Helpers shared by the tools that load a model beside the live router:
scripts/verify-build and ./add-model. Both must refuse to probe while the
service holds VRAM, need a spare port, and stop what they started, so the
rules live once here.

Stdlib only, like scripts/llm: nothing that uses the server may depend on
eval/.
"""

from __future__ import annotations

import configparser
import json
import socket
import subprocess
import urllib.error
import urllib.request
from pathlib import Path


def read_preset(path: Path) -> dict[str, dict[str, str]]:
    """Each model's settings from a built models.ini: `[*]` defaults, then its section."""
    text = Path(path).read_text(encoding="utf-8")
    cp = configparser.ConfigParser(interpolation=None, comment_prefixes=(";", "#"),
                                   default_section="__none__")
    cp.optionxform = str  # keep keys exactly as llama-server spells them
    cp.read_string("[__top__]\n" + text)
    base = dict(cp["*"]) if cp.has_section("*") else {}
    return {name: {**base, **dict(cp[name])} for name in cp.sections()
            if name not in ("*", "__top__")}


def service_busy(url: str) -> list[str]:
    """Models the live service has in VRAM right now. A stopped service is idle."""
    try:
        with urllib.request.urlopen(f"{url}/models", timeout=5) as resp:
            data = json.loads(resp.read().decode("utf-8")).get("data", [])
    except (urllib.error.URLError, OSError, ValueError):
        return []
    return [m["id"] for m in data
            if (m.get("status") or {}).get("value") in ("loaded", "loading")]


def port_free(port: int) -> bool:
    with socket.socket() as s:
        return s.connect_ex(("127.0.0.1", port)) != 0


def free_port(start: int = 8090, stop: int = 8130) -> int:
    for p in range(start, stop):
        if port_free(p):
            return p
    raise SystemExit("no free port between 8090 and 8130 for the fit probe")


def stop(proc: subprocess.Popen) -> None:
    if proc.poll() is None:
        proc.terminate()
        try:
            proc.wait(timeout=20)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()
