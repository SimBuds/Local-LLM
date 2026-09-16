# Rebuild-then-verify automation.
#
# The rule this enforces used to be manual discipline in README.md: "after
# editing anything in prompts/, memory/, or knowledge/, rebuild and run
# run-persona.py". Manual discipline is the kind that gets skipped on the one
# commit that breaks the identity rule, so it lives here instead.
#
#   make build   rebuild only the models whose prompt stack changed
#   make deploy  copy models/models.ini to ~/.config/llama.cpp/ for the llama-server service
#   make serve   start a router over the repo's models/models.ini (testing undeployed changes)
#   make check   rebuild those, then run the persona suite over all of them
#   make persona run the persona suite without rebuilding
#   make hook    install the pre-commit hook that runs `make check`
#   make clean   drop the build stamps, forcing a full rebuild next time
#
# Every builder assembles the SAME prompt stack, so any edit under prompts/,
# memory/, or knowledge/ invalidates every model. The per-model stamp still
# matters: it means editing one builder's PARAMS rebuilds only that model, and a
# no-op `make check` costs nothing but the persona run.

# Every build-* script is a model. Discovered rather than listed, so dropping in
# a new builder (see ./add-model) is enough; before 2026-09-15 this was a hand-
# maintained list and a new builder was silently ignored until someone edited it.
# build-common.sh is shared assembly, not a builder, so it is filtered out.
# The order here is the section order in models.ini. wildcard sorts, so it is
# gemma lite qwen rather than the hand-written gemma qwen lite. llama-server
# sorts /models itself and does not depend on file order.
MODELS  := $(filter-out common.sh,$(patsubst build-%,%,$(wildcard build-*)))
STACK   := $(shell find prompts memory knowledge -type f -name '*.md' 2>/dev/null | sort)
STAMPS  := $(addprefix models/,$(addsuffix /.built,$(MODELS)))
PRESETS := $(addprefix models/,$(addsuffix /preset.ini,$(MODELS)))

# Attempts per persona task in `make check`. Low by default so the hook stays
# usable as a gate; the real measurement is ./eval/run-profile.py.
ATTEMPTS ?= 3
PORT     ?= 8080
# The llama-server user service (systemd/llama-server.service) reads its preset
# from here. Override to deploy somewhere else, e.g. a scratch dir for testing.
DEPLOY_DIR ?= $(HOME)/.config/llama.cpp

.PHONY: all build deploy serve check persona hook clean FORCE

all: build

build: models/models.ini

# The router reads one preset file: the shared [*] settings, then one section
# per model. A running server does not reload it — restart `make serve`.
#
# FORCE because make cannot see a prerequisite that no longer exists: deleting a
# builder shrinks $(STAMPS), which never makes models.ini out of date, so the
# removed model kept its section in the router config and went on being served.
# Rebuilding the file every time costs one cat of four small files; the builders
# themselves are still guarded by their stamps.
models/models.ini: server.ini $(STAMPS) FORCE
	cat server.ini $(PRESETS) > $@

FORCE:

# A model is stale when the shared stack, the shared assembly, or its own
# builder is newer than its stamp. A rebuild only rewrites text files; the GGUF
# is never touched, so it is cheap.
models/%/.built: build-% build-common.sh $(STACK)
	@echo "=== rebuilding $* (prompt stack or builder changed) ==="
	./build-$*
	@mkdir -p $(dir $@) && touch $@

# The service serves the deployed copy, not the repo's, so a half-finished edit here
# never reaches the apps that depend on it. Restart the service after deploying:
# systemctl --user restart llama-server
deploy: models/models.ini
	mkdir -p $(DEPLOY_DIR)
	cp models/models.ini $(DEPLOY_DIR)/models.ini
	@echo "deployed to $(DEPLOY_DIR)/models.ini, now: systemctl --user restart llama-server"

# --models-max 1 keeps one model resident at a time, so each gets the whole card
# and a model's throughput never depends on what else happens to be loaded.
# While the service holds port 8080, try undeployed changes on another port:
# make serve PORT=8081, with LLM_URL=http://localhost:8081 for the eval runners.
serve: models/models.ini
	llama-server --models-preset models/models.ini --models-max 1 --port $(PORT)

check: build
	@echo "=== persona suite: does the rebuilt stack still hold? ==="
	./eval/run-persona.py --models $(MODELS) --attempts $(ATTEMPTS)

persona:
	./eval/run-persona.py --models $(MODELS) --attempts $(ATTEMPTS)

hook:
	@printf '%s\n' \
	  '#!/usr/bin/env bash' \
	  '# Installed by `make hook`. Rebuilds any model whose prompt stack changed' \
	  '# and runs the persona suite before the commit lands.' \
	  '# Skip a known-bad-but-intentional commit with: git commit --no-verify' \
	  'set -euo pipefail' \
	  'if git diff --cached --name-only | grep -qE "^(prompts|memory|knowledge)/|^build-"; then' \
	  '  echo "pre-commit: prompt stack touched — running make check"' \
	  '  exec make -C "$$(git rev-parse --show-toplevel)" check' \
	  'fi' \
	  > .git/hooks/pre-commit
	@chmod +x .git/hooks/pre-commit
	@echo "installed .git/hooks/pre-commit (bypass with git commit --no-verify)"

clean:
	rm -f $(STAMPS)
