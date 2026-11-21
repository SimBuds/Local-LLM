#!/usr/bin/env python3
"""
Unit tests for the tracked Pi coding-agent config in pi/. No Pi, no router and
no network: the two JSON files are read and compared with this repo. Run with:

    python3 -m unittest eval/test_pi_config.py -v

Why this exists: pi/models.json repeats facts this repo owns (the router's port,
its model names, the served context size). The copy is what a VM guest's Pi
reads, so a name or size that drifts from the router fails only inside the
guest, far from the change that caused it.

PI_CONFIG_DIR points the tests at another folder, so a check that breaks the
config on purpose runs against a scratch copy and never the tracked files.
"""

from __future__ import annotations

import json
import os
import re
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
PI_DIR = Path(os.environ.get("PI_CONFIG_DIR", REPO / "pi"))
PROVIDER = "local-llm"
# The guest reaches the router through an SSH reverse tunnel on its own
# localhost. The direct guest address, 10.0.2.2:8080, gets a 403: the router
# runs with --cors-origins localhost, which rejects any other Host header.
GUEST_ROUTER = "http://localhost:8080/v1"


def lineup() -> set[str]:
    """Router model names, discovered the way the Makefile does: one per build-*."""
    return {p.name.removeprefix("build-") for p in REPO.glob("build-*")
            if p.name != "build-common.sh"}


def served_ctx() -> int:
    m = re.search(r"^ctx-size\s*=\s*(\d+)\s*$", (REPO / "server.ini").read_text(encoding="utf-8"), re.M)
    assert m, "server.ini has no ctx-size line"
    return int(m.group(1))


class ModelsJsonTests(unittest.TestCase):
    def setUp(self):
        self.doc = json.loads((PI_DIR / "models.json").read_text(encoding="utf-8"))
        self.provider = self.doc["providers"][PROVIDER]
        self.models = self.provider["models"]

    def test_there_is_exactly_one_provider(self):
        self.assertEqual(list(self.doc["providers"]), [PROVIDER])

    def test_the_provider_is_the_router_through_the_guest_tunnel(self):
        self.assertEqual(self.provider["baseUrl"], GUEST_ROUTER)

    def test_the_provider_speaks_openai_chat_completions(self):
        self.assertEqual(self.provider["api"], "openai-completions")

    def test_the_key_is_a_placeholder_and_not_empty(self):
        # The router accepts any key. Pi's docs say a model without resolvable
        # credentials stays hidden, so the placeholder must be there.
        self.assertEqual(self.provider["apiKey"], "local")

    def test_at_least_one_model_is_listed(self):
        self.assertGreaterEqual(len(self.models), 1)

    def test_every_model_is_a_router_name_in_this_repo(self):
        for m in self.models:
            self.assertIn(m["id"], lineup(), f"{m['id']!r} has no build-* file")

    def test_no_model_is_listed_twice(self):
        ids = [m["id"] for m in self.models]
        self.assertEqual(len(ids), len(set(ids)))

    def test_every_context_window_is_the_served_context(self):
        for m in self.models:
            self.assertEqual(m["contextWindow"], served_ctx(), m["id"])


class SettingsJsonTests(unittest.TestCase):
    def setUp(self):
        self.settings = json.loads((PI_DIR / "settings.json").read_text(encoding="utf-8"))
        models = json.loads((PI_DIR / "models.json").read_text(encoding="utf-8"))
        self.ids = [m["id"] for m in models["providers"][PROVIDER]["models"]]

    def test_the_default_provider_is_the_router(self):
        self.assertEqual(self.settings["defaultProvider"], PROVIDER)

    def test_the_default_model_is_one_the_provider_lists(self):
        self.assertIn(self.settings["defaultModel"], self.ids)

    def test_pis_router_manager_is_turned_off(self):
        # Pi's built-in llama.cpp extension loads, unloads and downloads models
        # on a router. This router is a pinned service other apps depend on.
        self.assertIn("-builtin:llama.cpp", self.settings["extensions"])

    def test_install_telemetry_is_off(self):
        self.assertIs(self.settings["enableInstallTelemetry"], False)


if __name__ == "__main__":
    unittest.main()
