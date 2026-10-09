# Local LLM Handbook

This handbook takes you from nothing installed to chatting with a local AI model,
and then through the jobs you will do from time to time: downloading a new model
from HuggingFace and running it, changing how the models behave, and keeping the
engine up to date.

You need to be able to open a terminal and paste commands into it. Every command
block says where it runs and what it should print. The deeper detail lives in
[`README.md`](README.md), and this handbook links to the right section whenever
you want more.

---

## 1. What this is

This project runs AI language models **on your own computer**, with no cloud
service and nothing leaving the machine. One background program, the
**server**, keeps a model ready and answers requests at
`http://localhost:8080`. You talk to it from the terminal with a small command
called `llm`, and other apps and code editors can talk to it too.

It ships set up for three models:

| Name | Good at |
|---|---|
| `lite` | Speed. The fastest and the default for everyday questions. |
| `gemma` | Writing, content, and patient teaching. |
| `qwen` | Following instructions closely and using tools. |

Both `gemma` and `qwen` are good at coding. The measured comparison is in the
README under [*Current picks*](README.md#current-picks).

### Words used in this handbook

| Word | Meaning |
|---|---|
| **Model** | The AI itself: one large file of learned numbers. |
| **GGUF** | The file format the models come in, ending in `.gguf`. |
| **Quant** | A compressed version of a model, such as `Q4_K_M`. Smaller quants fit more easily and lose a little quality. |
| **HuggingFace** | The website most open models are downloaded from (huggingface.co). |
| **VRAM** | The graphics card's own memory. A model runs fastest when it fits there. |
| **Split** | How much of a model lives on the graphics card and how much in normal memory. Each model's split is measured once and then fixed. |
| **Server** or **router** | The background program that loads models and answers requests. It keeps one model loaded at a time. |
| **Prompt stack** | The instructions and personal context sent along with your question, so the model knows who it is talking to and how to answer. |
| **Token** | A piece of a word. Models read and write in tokens, and speed is counted in tokens per second. |
| **Port** | The number at the end of an address like `localhost:8080`. It picks which program on your computer answers. |
| **Checksum** | A long code computed from a file. If yours matches the published one, the download is complete and unchanged. |

---

## 2. First steps (once)

### 2.1 What you need

- A Linux computer with an **NVIDIA graphics card**. The settings here are tuned
  for an RTX 3080 with 10 GB. Another card works, but the splits have to be
  measured again (see [README, *Build And Tune*](README.md#build-and-tune)).
- **32 GB of normal memory.** The two bigger models keep part of themselves there.
- About **45 GB of free disk** for the three models.
- These programs. Check them with:

```bash
# Runs in: any terminal. Read-only, safe to repeat.
for t in git cmake nvcc make curl jq python3 sha256sum nvidia-smi; do
  if command -v "$t" >/dev/null; then echo "ok       $t"; else echo "MISSING  $t"; fi
done
```

Every line must start with `ok`. `nvcc` comes with NVIDIA's CUDA toolkit. Install
anything marked `MISSING` with your system's package manager before going on.

### 2.2 Get this project

This handbook assumes the project lives in `~/Apps/Local-LLM`. If you already
have the folder there, skip this step.

```bash
# Runs in: any terminal. Fails harmlessly until you edit the first line.
REPO_URL="https://example.invalid/your-copy-of-this-repo.git"   # edit this line
[ -d ~/Apps/Local-LLM/.git ] && echo "already there, skipping" \
  || git clone "$REPO_URL" ~/Apps/Local-LLM
```

### 2.3 Build the engine (llama.cpp)

The server is `llama-server`, part of the open-source llama.cpp project. It is
built from source so it can use your graphics card. This takes a few minutes.

```bash
# Runs in: any terminal, as yourself (no sudo). Safe to re-run.
SRC="$HOME/src/llama.cpp"
[ -d "$SRC/.git" ] || git clone https://github.com/ggml-org/llama.cpp "$SRC"
cmake --fresh -S "$SRC" -B "$SRC/build" -DGGML_CUDA=ON -DCMAKE_CUDA_ARCHITECTURES=86 \
  -DBUILD_SHARED_LIBS=OFF -DCMAKE_BUILD_TYPE=Release \
  && cmake --build "$SRC/build" -j 20 --target llama-server \
  && mkdir -p "$HOME/.local/bin" \
  && ln -sfn "$SRC/build/bin/llama-server" "$HOME/.local/bin/llama-server" \
  && llama-server --version
```

The last line must print `version:` and a build number. `86` is the code for an
RTX 30-series card. For another card, look up its "CUDA compute capability" and
use that number without the dot. If `llama-server` is "not found",
`~/.local/bin` is not on your `PATH`. Add it in your shell's startup file and
open a new terminal.

### 2.4 Get the models

Models live outside the project, in `~/models/gguf`. The project expects these
three files:

| Name | File |
|---|---|
| `lite` | `qwen3.5-9b-mtp-q4_K_M.gguf` |
| `gemma` | `gemma4-26b-a4b-it-qat.gguf` |
| `qwen` | `qwen3.6-35b-a3b-mtp-q4_K_M.gguf` |

`lite` downloads directly. It is about 6 GB, and the command resumes if it is
interrupted:

```bash
# Runs in: any terminal. Safe to re-run, it continues a partial download.
mkdir -p ~/models/gguf
curl -L --fail -C - -o ~/models/gguf/qwen3.5-9b-mtp-q4_K_M.gguf \
  https://huggingface.co/unsloth/Qwen3.5-9B-MTP-GGUF/resolve/main/Qwen3.5-9B-Q4_K_M.gguf
```

The `gemma` and `qwen` files were copied from an older setup and have no exact
public download. The README's [*Model Files*](README.md#model-files) section
names the closest downloads and the checksums of the originals. If you use a
different file, set its name in `BASE_MODEL` inside `build-gemma` or
`build-qwen`, and measure its split again (README,
[*Build And Tune*](README.md#build-and-tune)).

### 2.5 Tell the models who you are

The prompt stack includes a short profile of you: background, skills, and how you
like answers. It stays private and is never uploaded with the project. Start from
the templates:

```bash
# Runs in: ~/Apps/Local-LLM. Safe to re-run, it never overwrites your edits.
cd ~/Apps/Local-LLM
[ -f memory/user.md ] || cp memory/user.example.md memory/user.md
[ -f memory/learning-profile.md ] || cp memory/learning-profile.example.md memory/learning-profile.md
```

Then open `memory/user.md` and `memory/learning-profile.md` in any text editor
and fill in each section.

The rule files in `prompts/` address the project's owner by name ("Casey"). If
you are setting this up for yourself, swap in your own name:

```bash
# Runs in: ~/Apps/Local-LLM. Does nothing until you edit the first line.
YOUR_NAME="YOUR_NAME"   # edit this line, for example YOUR_NAME="Sam"
if [ "$YOUR_NAME" = "YOUR_NAME" ]; then echo "Edit the YOUR_NAME line first."
else sed -i "s/Casey/$YOUR_NAME/g" prompts/*.md && grep -c "$YOUR_NAME" prompts/*.md
fi
```

It prints a count per file. Then read `prompts/system.md` once and change any
"he" that should be "she" or "they".

### 2.6 Build and start the server

`make build` assembles each model's settings and prompt stack, and
`make deploy` copies the result to where the server reads it. The server then
runs as a background service under your own user, starting at login.

```bash
# Runs in: ~/Apps/Local-LLM, as yourself (no sudo). Safe to re-run.
cd ~/Apps/Local-LLM
make build
make deploy
mkdir -p ~/.config/systemd/user
cp systemd/llama-server.service ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now llama-server
systemctl --user is-active llama-server
ok=no; for i in $(seq 1 30); do curl -sf localhost:8080/health && { ok=yes; echo; break; }; sleep 1; done
[ "$ok" = yes ] || echo "The server did not answer within 30 seconds. Check: journalctl --user -u llama-server -n 50"
```

It must print `active` and then `{"status":"ok"}`. The last two lines wait up to
30 seconds for that answer, because the server needs a moment to start
listening. If `make build` stops with an error, it says what is missing. See
[section 8](#8-when-something-goes-wrong).

### 2.7 Install `llm` and say hello

```bash
# Runs in: ~/Apps/Local-LLM. Safe to re-run.
install -m 755 scripts/llm ~/.local/bin/llm
llm status
llm chat lite "Say hello in one short sentence."
```

`llm status` shows `up` and lists `gemma`, `lite`, and `qwen`. The first chat
takes a few seconds while the model loads, then the answer appears word by word.
Setup is done.

---

## 3. Daily use

### Ask a question

```bash
# Runs in: any terminal.
llm chat lite "What is the difference between RAM and VRAM?"
llm chat --think qwen "Plan a three-step refactor of a 2,000-line Python file."
```

`--think` lets the model reason first. The reasoning goes to the screen and only
the answer goes to a file if you add `> answer.txt`. Leave out the question and
`llm chat` reads it from what you pipe in: `cat notes.txt | llm chat gemma`.

For a back-and-forth conversation, leave out the question in a terminal:

```bash
# Runs in: any terminal. Type at the >>> prompt.
llm chat lite
```

The model remembers earlier turns in the same session. **Ctrl-C** while it is
answering stops that answer, and **Ctrl-C** or **Ctrl-D** at an empty `>>>`
prompt ends the session.

By default `llm chat` sends **only your question**, without the prompt stack. To
get answers shaped by your profile and the rules, send the stack along:

```bash
# Runs in: ~/Apps/Local-LLM.
llm chat --system-file models/qwen/prompt.txt qwen "Who are you?"
```

### See what is running and free the graphics card

| You want to | Command |
|---|---|
| See which model is loaded and how much VRAM it holds | `llm status` |
| Load a model ahead of time | `llm load gemma` |
| Free the graphics card now, for a game or other app | `llm unload` |
| Read the server's log | `llm logs` (Ctrl-C to stop) |
| Stop the server completely | `systemctl --user stop llama-server` |
| Start it again | `systemctl --user start llama-server` |

You rarely need `unload`: a model that sits unused for 10 minutes goes to
**sleep** and gives back its VRAM by itself. The next question wakes it.

Only one model is loaded at a time. Asking a different model unloads the current
one first, which takes a few seconds.

### Connect another app or a code editor

Anything that speaks the OpenAI chat format can use the server:

- **Address:** `http://localhost:8080/v1`
- **Model:** `gemma`, `qwen`, or `lite`
- **API key:** any non-empty text, such as `local`
- **Longest input:** 65,536 tokens. Longer requests are refused, never cut short.

The app has to send its own instructions. Nothing is built into the model. For
the VS Code extensions Continue and Cline, follow the README's
[*Use In VSCode*](README.md#use-in-vscode-continue--cline) section.

---

## 4. Running a new model from HuggingFace

This is the full path from finding a model online to using it like the other
three. Most of the time goes to the download.

### Step 1. Choose a model

On huggingface.co, look for a repository whose name ends in `GGUF`, for example
`unsloth/Qwen3.5-9B-MTP-GGUF`. Note its name in the form `Org/Repo`.

Two rules of thumb for a 10 GB card:

- **Pick a `Q4_K_M` quant** unless you have a reason not to. It is the usual
  balance of size and quality.
- **Size decides speed.** A file under about 6 GB fits on the card and runs fast.
  A bigger file runs partly from normal memory. That is fine for
  "mixture of experts" models, whose names usually carry a part like `A3B` or
  `A4B`, and slow for the rest.

Some models are **gated**: you must accept their terms on the model's
HuggingFace page, signed in, before the download works.

### Step 2. Download it

`add-model` lists the repository's files with their sizes and prints the exact
download commands for the one you pick. It never downloads by itself.

```bash
# Runs in: ~/Apps/Local-LLM. Safe to repeat. Left unedited, it stops with "was not found".
cd ~/Apps/Local-LLM
HF_REPO="Org/Repo-GGUF"   # edit this line, for example HF_REPO="unsloth/Qwen3.5-9B-MTP-GGUF"
./add-model "$HF_REPO"
```

Type the number of the file you want. It prints two blocks:

1. **The download.** Copy and run it. It resumes if interrupted.
2. **The checksum check.** Run it after the download. Every line must print `OK`.
   Anything else means a broken or wrong download. Run the download block again.

### Step 3. Set it up

Free the graphics card first, because the setup measures how much of the model
fits, and anything else on the card makes that answer too small:

```bash
# Runs in: ~/Apps/Local-LLM.
llm unload
./add-model "$HF_REPO"   # pick the same number again, it is now downloaded and carries on
```

If your terminal was closed since step 2, set `HF_REPO` again first. It then
asks for a **router name**: the short name you and your apps will use, such as
`mistral`. Use lowercase letters and digits, starting with a letter. `gemma`, `qwen`, and `lite`
are taken.

Then it loads the model once to measure its split, which takes from seconds to a
few minutes. It ends by printing `Wrote build-<name>` and a summary.

### Step 4. Build it

```bash
# Runs in: ~/Apps/Local-LLM. Safe to re-run.
make build
```

### Step 5. Try it without disturbing anything

Run a second, temporary server on port 8081 so the main one and any app using it
keep working. Use two terminals.

```bash
# Terminal A. Runs in: ~/Apps/Local-LLM. Leave it running, Ctrl-C stops it.
llm unload
make serve PORT=8081
```

```bash
# Terminal B. Runs in: any terminal.
NEW_MODEL="mymodel"   # edit this line to your router name
LLM_URL=http://localhost:8081 llm chat "$NEW_MODEL" "Introduce yourself in two sentences."
```

For a quick quality check (5 to 10 minutes), run the smoke tests against it. The
results go to a scratch folder, so they don't touch the project's scoreboard:

```bash
# Terminal B. Runs in: ~/Apps/Local-LLM, with terminal A still running.
NEW_MODEL="mymodel"   # edit this line to your router name
LLM_URL=http://localhost:8081 ./eval/run-profile.py smoke --models "$NEW_MODEL" lite \
  --out-root /tmp/smoke-"$NEW_MODEL"
```

It ends by listing a `summary.md` per test. Open them to compare the new model
with `lite`. Press Ctrl-C in terminal A when you are done.

### Step 6. Make it available everywhere

Copy the new settings to the main server and restart it. The restart drops any
request in progress, so pick a moment when no app is busy.

```bash
# Runs in: ~/Apps/Local-LLM.
make deploy && systemctl --user restart llama-server
ok=no; for i in $(seq 1 30); do curl -sf localhost:8080/health && { ok=yes; echo; break; }; sleep 1; done
[ "$ok" = yes ] || echo "The server did not answer within 30 seconds. Check: llm logs"
llm status
```

It prints `{"status":"ok"}` once the server is back, and then `llm status`
lists your new model next to the others.

To remove a model later, delete its `build-<name>` file, then run
`make build`, `make deploy`, and the restart above.

---

## 5. Changing how the models behave

What the models know about you is in `memory/`. How they answer is in
`prompts/`. Both are plain text files. Every model reads the same stack.

1. Edit a file in `memory/` or `prompts/`.
2. Rebuild and check that the rules still hold:

   ```bash
   # Runs in: ~/Apps/Local-LLM. Takes a few minutes, loads each model in turn.
   make check
   ```

   It ends with a `Summary:` path. Open that file and read *Violations observed*:
   it says `None` when every answer obeyed every rule.
   To try an idea without recording it, add a scratch folder:
   `make check OUT_ROOT=/tmp/try`.
3. When you are happy, ship it:

   ```bash
   # Runs in: ~/Apps/Local-LLM.
   make deploy && systemctl --user restart llama-server
   ```

Keep rules short. Every word is sent with every question that uses the stack,
so a long stack is slower and can confuse smaller models.

---

## 6. Keeping the engine up to date

llama.cpp improves most days. Update about once a week. The new version is built
**beside** the running one and checked before it replaces anything, so a bad
update never reaches your apps.

```bash
# Runs in: ~/Apps/Local-LLM. Writes only inside ~/src/llama.cpp. Safe to re-run.
./scripts/llama-update status   # what is installed, and what is new
./scripts/llama-update build    # build the new version beside the running one
```

`build` ends by printing the new and the running version. Then check the new one.
It refuses to start while a model is loaded, because it needs the graphics card:

```bash
# Runs in: ~/Apps/Local-LLM. Takes about 10 minutes.
llm unload
./scripts/verify-build
```

It ends with `PASS` or `FAIL`. On `PASS`, switch over and restart:

```bash
# Runs in: ~/Apps/Local-LLM. Run both lines together.
./scripts/llama-update swap
systemctl --user restart llama-server && systemctl --user is-active llama-server
```

The last line must print `active`. If the new version misbehaves, go back:

```bash
# Runs in: ~/Apps/Local-LLM.
./scripts/llama-update rollback
systemctl --user restart llama-server && systemctl --user is-active llama-server
```

On `FAIL`, keep the running version. The output says which check failed and
where its log is.

---

## 7. Comparing models

The project scores every model on speed, coding, writing, structured output,
tool use, teaching, and how well it follows the prompt stack:

```bash
# Runs in: ~/Apps/Local-LLM.
./eval/run-profile.py smoke    --models gemma qwen lite   # 5 to 10 minutes, a quick sanity check
./eval/run-profile.py standard --models gemma qwen lite   # about an hour, the real comparison
./eval/promote.py --dry-run                               # preview the new scoreboard
./eval/promote.py                                         # write it into README.md
```

Don't restart the server while a run is going, or the run is wasted. A run that
only checks things still work belongs in a scratch folder (`--out-root /tmp/x`),
because the newest run becomes the scoreboard. How each test scores is in the
README under [*Evaluation*](README.md#evaluation).

---

## 8. When something goes wrong

| What you see | What it means | What to do |
|---|---|---|
| `llm` says `no llama-server router at ...` | The server is not running | `systemctl --user start llama-server`, then `llm logs` if it stops again |
| `couldn't bind HTTP server socket` from `make serve` | Something already uses that port, usually the main server on 8080 | Use `make serve PORT=8081` |
| `make build`: `GGUF for '<name>' not found` | The model file is missing or misnamed | Put it in `~/models/gguf`, or fix `BASE_MODEL` in that `build-*` file |
| `make build`: `no personal context in memory/` | The profile files were never created | Repeat [step 2.5](#25-tell-the-models-who-you-are) |
| `add-model`: `holds <model> in VRAM` | A model is loaded, which would spoil the measurement | `llm unload`, then run it again |
| `add-model`: `already has a builder` | That file is already set up under another name | Use that name, or edit its `build-*` file |
| `add-model`: `was not found, or is gated or private` | The name is misspelled, or the model needs its terms accepted (HuggingFace answers both the same way) | Check the `Org/Repo` spelling on the model's page. If it is right, accept the terms there while signed in, then retry |
| Checksum line says `FAILED` | The download is broken or incomplete | Run the download command again, then the check |
| `CUDA error: out of memory` in `llm logs` | Another program is using the graphics card | Close it (`nvidia-smi` lists them), then ask again |
| The model ignores your profile or rules | The question went without the prompt stack | Use `--system-file models/<name>/prompt.txt`, or set it as the app's system message |
| An edit to `prompts/` changed nothing | The running server still has the old version | `make build && make deploy && systemctl --user restart llama-server` |
| An eval run stops with `model ... crashed in the router` | The graphics card hit a fault | Check `journalctl -k -b \| grep -i xid`, then rerun. A repeat after an update is a reason to roll back ([section 6](#6-keeping-the-engine-up-to-date)) |
| The first answer takes several seconds | The model is loading or waking from sleep | Normal. Later answers are quick. |
| `exceed_context_size_error` | The input is longer than 65,536 tokens | Send less text at once |

---

## 9. Where things live

| What | Where | Changed by |
|---|---|---|
| Your profile | `memory/user.md`, `memory/learning-profile.md` | You, by hand (private, never committed) |
| Answer rules | `prompts/*.md` | You, by hand |
| One file per model | `build-<name>` | `./add-model`, or you |
| Settings shared by every model | `server.ini` | You, rarely (see the README first) |
| Built output | `models/` | `make build` |
| Test results | `eval/runs/` | The test runs |
| The model files | `~/models/gguf/` | Your downloads |
| The engine | `~/src/llama.cpp`, linked from `~/.local/bin/llama-server` | Steps 2.3 and 6 |
| The live settings the server reads | `~/.config/llama.cpp/models.ini` | `make deploy` |
| The background service | `~/.config/systemd/user/llama-server.service` | Step 2.6 |

### Going deeper

- [`README.md`](README.md): how everything works, every command, the scoreboard.
  The sections to start with are [*Adding A Model*](README.md#adding-a-model),
  [*Build And Tune*](README.md#build-and-tune),
  [*Serving other apps*](README.md#serving-other-apps), and
  [*Terminal helper*](README.md#terminal-helper-llm).
- [`PLAN.md`](PLAN.md): why the project is built the way it is, and the
  decisions that must not change without a reason.
- [`TESTING.md`](TESTING.md): every test, and the full history of results.
