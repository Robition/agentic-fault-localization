# 4090 machine: serving setup and validation

**Who this is for:** the person who owns the RTX 4090 machine (Windows 11 Home), or their coding agent.
**Goal:** a fast, pinned model server and a validated environment, so M3 to M5 can run here as soon as the code lands.
**Rules:** work on the branch `serving/4090`. Edit only `docs/serving.md` (create it). Don't touch `src/`, `tests/`, `data/`, or `configs/`. That code is being written on another machine in parallel. Run all commands in PowerShell.

Each task ends with what to record in `docs/serving.md`. Paste the raw command output; don't summarize it.

---

## Task 1: Repository runs natively on Windows

```powershell
winget install --id=astral-sh.uv -e      # skip if uv is already installed
git clone <repo-url>; cd agentic-fault-localization
git checkout -b serving/4090
uv sync --frozen
uv run execloc --help
uv run pytest
```

**Done when** all 25 tests pass. **Record:** the `uv --version` output and the last line of the pytest output.

---

## Task 2: Fix the slow generation speed

A first test measured 9.9 tokens/s for generation and 46 tokens/s for prompt processing. A 4090 running a 24B 4-bit model should reach roughly 40 or more and well over 1000, respectively.

**Likely cause:** `nvidia-smi` showed 4.4 GB of GPU memory already in use before the model loaded. The model takes 19 GB at a 32K context, so together they nearly fill the 24 GB card. In WDDM mode the NVIDIA driver can quietly move overflow into system RAM. Ollama still reports "100% GPU", but everything runs far slower.

Fix it in this order, and re-test after each step (see Task 4):

1. Open the NVIDIA Control Panel, go to Manage 3D settings, and set **CUDA - Sysmem Fallback Policy** to **Prefer No Sysmem Fallback**. Apply it, then restart Ollama.
2. Close apps that use GPU memory, such as browsers, game launchers, and Discord. Check `nvidia-smi` to see the idle usage before you load the model.
3. Shrink the context cache so the model fits with room to spare. Use the `setx` commands in Task 3; they set `OLLAMA_FLASH_ATTENTION` and `OLLAMA_KV_CACHE_TYPE`.
4. If it still doesn't fit, `ollama ps` will now show a CPU/GPU split instead of hiding it. Report that split and stop; don't change the model.

**Record:** `nvidia-smi` at idle, and `ollama ps` with the model loaded, after each fix.

---

## Task 3: Pin the server settings

Ollama on Windows reads environment variables at startup. Set them for your user account, then quit Ollama from the system tray and start it again.

```powershell
setx OLLAMA_CONTEXT_LENGTH 32768     # stops runs from silently falling back to a smaller context
setx OLLAMA_KEEP_ALIVE 24h           # stops the 2.5-minute reload after every idle gap
setx OLLAMA_FLASH_ATTENTION 1
setx OLLAMA_KV_CACHE_TYPE q8_0       # halves the context cache's memory; needs flash attention
```

Then check the settings through the OpenAI-compatible endpoint, which is what the experiment uses. This check must not rely on `ollama run`.

```powershell
curl.exe http://localhost:11434/v1/models
ollama ps          # after a /v1 request, CONTEXT must read 32768
```

To trigger a /v1 request:

```powershell
curl.exe http://localhost:11434/v1/chat/completions -H "Content-Type: application/json" -d '{\"model\":\"devstral-small-2\",\"messages\":[{\"role\":\"user\",\"content\":\"hi\"}],\"temperature\":0}'
```

**Record:** the `/v1/models` output, and `ollama ps` after the /v1 request.

---

## Task 4: Speed test with the model already loaded

```powershell
ollama run devstral-small-2 --verbose "Write a 300-word explanation of Python's GIL."
ollama run devstral-small-2 --verbose "Write a 300-word explanation of Python's GIL."
```

**Record:** the `--verbose` statistics from the **second** run.
**Target:** generation of roughly 40 tokens/s or more, and prompt processing in the high hundreds of tokens/s or more. If you're far below that after Task 2, say so in `docs/serving.md`; that decides whether 200 runs are practical.

---

## Task 5: Pin the exact model build

```powershell
ollama list
ollama show devstral-small-2
ollama show devstral-small-2 --modelfile
```

**Record:** all three outputs. `meta.json` needs the digest (it was `24277f07f62d`), the quantization, the parameter count, and the context length.

---

## Task 6: Docker checks for the two arms

Docker Desktop on Windows 11 Home uses the WSL2 backend. Both arms need Linux containers.

```powershell
docker version
docker info --format "{{.OSType}} {{.Architecture}} {{.DockerRootDir}}"
docker run --rm hello-world

# Static arm: can a read-only mount of a Windows folder be read inside a container?
mkdir C:\temp\mounttest; echo hi > C:\temp\mounttest\a.txt
docker run --rm -v C:\temp\mounttest:/testbed:ro alpine sh -c "cat /testbed/a.txt && (touch /testbed/b 2>&1 || true)"
```

The last command must print `hi` and then a read-only error.

**Exec arm image pull:** get the official image name for `astropy__astropy-13579` from mini-swe-agent's own code, in `get_swebench_docker_image_name` in `.venv\Lib\site-packages\minisweagent\run\benchmarks\swebench.py`. Read it rather than guessing; it rewrites the instance ID before building the name. Then pull it with `docker pull`, and confirm `docker run --rm <image> python --version` works.

**Record:** all outputs, the image name and where you found it, its size, and the time the pull took. Also record free disk space on the drive in `DockerRootDir`. Each instance image is several GB.

---

## Task 7: Send it back

```powershell
git add docs/serving.md
git commit -m "4090 serving and validation results"
git push -u origin serving/4090
```

Open a PR or tell the team. Don't merge anything into `main` that touches files outside `docs/`.
