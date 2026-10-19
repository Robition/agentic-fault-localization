# RTX 4090 serving and validation results

Validation date: 2026-10-19 (America/Chicago). Operator: Caden Sibert.
Repository: `Robition/agentic-fault-localization`; branch: `serving/4090`; base commit: `0462a39f81986a9954e9a7bf8e0959f92e4c108c`.

## Result and scope

The warm second generation run reached **50.77 tokens/s**, above the runbook's roughly 40 tokens/s target. All **25 Windows tests passed**. The loaded model reports **100% GPU**, active context **32768**, and **24-hour** keep-alive after an actual OpenAI-compatible request from Windows. The Windows folder mount is readable and rejects writes. The official Astropy SWE-bench image pulls and runs Python successfully.

This machine runs the project natively on Windows, while Ollama runs as a systemd service in Ubuntu/WSL2. The runbook's Windows `setx` instructions were therefore applied as equivalent persistent systemd settings on the server that actually serves requests. There is no native Windows Ollama installation involved in these measurements. Commands below are launched in PowerShell; Linux commands use `wsl -d Ubuntu ...`.

Only `docs/serving.md` is changed in this repository. Model files, installed dependencies, scratch mount data, and machine configuration are outside the tracked source tree. No project source, tests, data, configs, lockfile, or model weights were changed.

Evidence provenance: the operator supplied the final second-run timing, the 4850 MiB idle snapshot, and confirmation of the NVIDIA setting while following the instructions interactively. The agent directly ran the Windows tests, collected the baseline error and machine data, rechecked the saved server settings and live model, tested Windows `/v1` connectivity, and completed the Docker checks. Outputs below are preserved as captured; excerpts are labeled.

## Machine and software

- GPU: NVIDIA GeForce RTX 4090, 24564 MiB, WDDM, driver 610.88.
- CPU: Intel Core i7-14700K, 20 cores / 28 logical processors.
- Physical RAM: 34,055,049,216 bytes.
- Project interpreter: native Windows CPython 3.12.14.
- Ollama: 0.40.3; model runner: `llamacpp`.
- mini-swe-agent: 2.4.6, installed by the frozen project lockfile.

Raw platform output (`wsl --version`, Ubuntu release, Ollama version, Windows CIM properties):
```text
WSL version: 3.0.3.0

Kernel version: 6.18.54.1-1

WSLg version: 1.0.79

MSRDC version: 1.2.7391

Direct3D version: 1.611.1-81528511

DXCore version: 10.0.26100.1-240331-1435.ge-release

Windows version: 10.0.26200.9711


PRETTY_NAME="Ubuntu 26.04.1 LTS"
NAME="Ubuntu"
VERSION_ID="26.04"
VERSION="26.04.1 LTS (Resolute Raccoon)"
VERSION_CODENAME=resolute
ollama version is 0.40.3

Caption     : Microsoft Windows 11 Home
Version     : 10.0.26200
BuildNumber : 26200


TotalPhysicalMemory : 34055049216
```

Ubuntu is stored at `D:\WSL\Ubuntu`. Docker's image data remains on `C:`; the exact host path and remaining capacity appear in Task 6.

## Task 1 — Native Windows repository validation

Ran from `C:\Users\caden\Desktop\UIUC School\CS527\agentic-fault-localization`:
```powershell
$env:UV_CACHE_DIR = 'D:\CS527-cache\uv'
$env:UV_PYTHON = 'C:\Users\caden\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe'
$env:PATH = 'C:\Users\caden\Desktop\UIUC School\CS527\project-setup\windows-python-tools\bin;' + $env:PATH
uv --version
uv sync --frozen
uv run execloc --help
uv run pytest
```

`uv` is installed in the portable tools directory above. A new PowerShell session needs that PATH entry, or a normal Windows uv installation. The download cache is on `D:`; the ignored `.venv` is in the repository on `C:`.

Raw `uv --version`:
```text
uv 0.13.1 (62faed335 2026-10-13 x86_64-pc-windows-msvc)
```

`uv sync --frozen` exited 0, built the local package, and installed 89 locked packages. Raw installation timing/warning excerpt:
```text
Prepared 89 packages in 33.44s
warning: Failed to hardlink files; falling back to full copy. This may lead to degraded performance.
         If the cache and target directories are on different filesystems, hardlinking may not be supported.
         If this is intentional, set `export UV_LINK_MODE=copy` or use `--link-mode=copy` to suppress this warning.
Installed 89 packages in 4.49s
```

The cross-volume hardlink warning caused a copy fallback; the sync and tests succeeded.

Raw `uv run execloc --help`:
```text
usage: execloc [-h] COMMAND ...

Does code execution help an LLM agent localize bugs?

positional arguments:
  COMMAND
    doctor    check this machine's environment
    sample    draw the frozen instance sample from SWE-bench Verified
    gold      build gold locations from the sampled instances' patches
    run       run one arm of the experiment
    score     score a run's predictions against gold

options:
  -h, --help  show this help message and exit
```

Raw `uv run pytest`:
```text
============================= test session starts =============================
platform win32 -- Python 3.12.14, pytest-9.1.1, pluggy-1.6.0
rootdir: C:\Users\caden\Desktop\UIUC School\CS527\agentic-fault-localization
configfile: pyproject.toml
testpaths: tests
plugins: anyio-4.15.1, platformdirs-4.12.3
collected 25 items

tests\test_gold.py .........                                             [ 36%]
tests\test_score.py ................                                     [100%]

============================= 25 passed in 5.37s ==============================
```

## Task 2 — Baseline failure and memory tuning

The first fresh baseline attempted the runbook's GIL prompt with the existing 32768 context, before the Flash Attention/q8_0 changes. It failed during model loading, so it produced no valid generation rate and no loaded `ollama ps` row. The first attempt exited 1 after 170.881 seconds; the second baseline run was not started. The runbook's historical 9.9 tokens/s value is not a new controlled baseline from this session.

Raw pre-load `nvidia-smi`:
```text
Mon Oct 19 15:35:47 2026
+-----------------------------------------------------------------------------------------+
| NVIDIA-SMI 610.88                 KMD Version: 610.88        CUDA UMD Version: 13.3     |
+-----------------------------------------+------------------------+----------------------+
| GPU  Name                  Driver-Model | Bus-Id          Disp.A | Volatile Uncorr. ECC |
| Fan  Temp   Perf          Pwr:Usage/Cap |           Memory-Usage | GPU-Util  Compute M. |
|                                         |                        |               MIG M. |
|=========================================+========================+======================|
|   0  NVIDIA GeForce RTX 4090      WDDM  |   00000000:01:00.0  On |                  Off |
|  0%   45C    P8             30W /  450W |    4276MiB /  24564MiB |     38%      Default |
|                                         |                        |                  N/A |
+-----------------------------------------+------------------------+----------------------+

+-----------------------------------------------------------------------------------------+
| Processes:                                                                              |
|  GPU   GI   CI              PID   Type   Process name                        GPU Memory |
|        ID   ID                                                               Usage      |
|=========================================================================================|
|    0   N/A  N/A            6188    C+G   ...\codex-computer-use-swift.exe      N/A      |
|    0   N/A  N/A            6288    C+G   ...ed_copilot\copilotapphost.exe      N/A      |
|    0   N/A  N/A            7420    C+G   ...ntrolPanel\SystemSettings.exe      N/A      |
|    0   N/A  N/A           10796    C+G   ...s\Mozilla Firefox\firefox.exe      N/A      |
|    0   N/A  N/A           11864    C+G   ...lientElectron\Riot Client.exe      N/A      |
|    0   N/A  N/A           12500    C+G   ...lpaper_engine\wallpaper64.exe      N/A      |
|    0   N/A  N/A           13756    C+G   C:\Windows\explorer.exe               N/A      |
|    0   N/A  N/A           13804    C+G   ...5n1h2txyewy\TextInputHost.exe      N/A      |
|    0   N/A  N/A           14036    C+G   ...2txyewy\CrossDeviceResume.exe      N/A      |
|    0   N/A  N/A           15144    C+G   ...kyb3d8bbwe\EdgeGameAssist.exe      N/A      |
|    0   N/A  N/A           15328    C+G   ..._cw5n1h2txyewy\SearchHost.exe      N/A      |
|    0   N/A  N/A           15352    C+G   ...y\StartMenuExperienceHost.exe      N/A      |
|    0   N/A  N/A           16092    C+G   ...8bbwe\PhoneExperienceHost.exe      N/A      |
|    0   N/A  N/A           17008    C+G   ...em32\ApplicationFrameHost.exe      N/A      |
|    0   N/A  N/A           17556    C+G   ...2p2nqsd0c76g0\app\ChatGPT.exe      N/A      |
|    0   N/A  N/A           17944    C+G   ....0.4283.45\msedgewebview2.exe      N/A      |
|    0   N/A  N/A           18940    C+G   ...engine\bin\webwallpaper64.exe      N/A      |
|    0   N/A  N/A           20572    C+G   ...xyewy\ShellExperienceHost.exe      N/A      |
|    0   N/A  N/A           21324    C+G   ...cord\app-1.0.9262\Discord.exe      N/A      |
|    0   N/A  N/A           24588    C+G   ...ot\Application\copilotapp.exe      N/A      |
|    0   N/A  N/A           25576    C+G   ...t\Edge\Application\msedge.exe      N/A      |
|    0   N/A  N/A           27620    C+G   ...roadcast\NVIDIA Broadcast.exe      N/A      |
|    0   N/A  N/A           29308    C+G   ...are\IntelGraphicsSoftware.exe      N/A      |
|    0   N/A  N/A           29348    C+G   ...indows\codex-computer-use.exe      N/A      |
|    0   N/A  N/A           29396    C+G   ...2p2nqsd0c76g0\app\ChatGPT.exe      N/A      |
|    0   N/A  N/A           31040    C+G   ...indows\System32\ShellHost.exe      N/A      |
|    0   N/A  N/A           31248    C+G   ...\cef.win64\steamwebhelper.exe      N/A      |
|    0   N/A  N/A           31812    C+G   ...s\Mozilla Firefox\firefox.exe      N/A      |
|    0   N/A  N/A           33736    C+G   ...s\Mozilla Firefox\firefox.exe      N/A      |
+-----------------------------------------------------------------------------------------+
```

Raw error excerpt (terminal spinner/control sequences omitted):
```text
Error: 500 Internal Server Error: llama-server startup failed after projector CPU offload retry: llama-server process has terminated: exit status 1: cudaMalloc failed: out of memory
ggml_backend_buft_alloc_buffer_n_default: failed to allocate CUDA0 buffer of size 13945835520
error loading model: unable to allocate CUDA0 buffer
```

Machine pressure observed during that failure: `C:` free space fell to about 0.25 GiB, and Windows reported a 20,808 MiB allocated pagefile with 3,778 MiB in use. The operator subsequently freed about 100 GB before the successful final test. The evidence does not isolate whether disk/commit pressure, competing GPU use, or cache allocation caused the earlier CUDA failure.

Changes and observations:

1. NVIDIA Control Panel initially displayed `CUDA - Sysmem Fallback Policy: Driver Default`. The operator then confirmed selecting **Prefer No Sysmem Fallback** under Global Settings and clicking Apply. This is operator-confirmed; no post-Apply screenshot was captured.
2. The operator was asked to close GPU-heavy apps before the next idle check. The exact set of closed apps was not recorded. Raw operator-supplied idle output after the resource-cleanup step:
```text
memory.used [MiB], memory.total [MiB]
4850 MiB, 24564 MiB
```

3. The four persistent settings in Task 3 were saved and Ollama restarted. The loaded footprint became 17 GB according to `ollama ps`; runner logs confirm q8_0 K/V caches and Flash Attention.

The individual changes were not benchmarked separately. Per-change loaded/idle snapshots beyond those shown here were not collected, so the final speed cannot be attributed to one setting. NVIDIA documents the fallback policy for native Windows applications; its effect on this WSL Ollama process was not independently established. [NVIDIA guidance](https://nvidia.custhelp.com/app/answers/detail/a_id/5490).

Final loaded GPU snapshot from the agent's verification:
```text
name, driver_version, memory.total [MiB], memory.used [MiB], utilization.gpu [%]
NVIDIA GeForce RTX 4090, 610.88, 24564 MiB, 22632 MiB, 15 %
```

The final `ollama ps` output is in Task 3. No CPU/GPU split was observed after the combined changes.

## Task 3 — Persistent configuration and `/v1` verification

Saved file: `/etc/systemd/system/ollama.service.d/serving.conf` inside Ubuntu:
```ini
[Service]
Environment="OLLAMA_CONTEXT_LENGTH=32768"
Environment="OLLAMA_KEEP_ALIVE=24h"
Environment="OLLAMA_FLASH_ATTENTION=1"
Environment="OLLAMA_KV_CACHE_TYPE=q8_0"
```

The operator ran `sudo systemctl daemon-reload` and `sudo systemctl restart ollama` after saving it. Raw effective settings:
```text
OLLAMA_CONTEXT_LENGTH=32768
OLLAMA_KEEP_ALIVE=24h
OLLAMA_FLASH_ATTENTION=1
OLLAMA_KV_CACHE_TYPE=q8_0
```

These settings survive Ollama restarts. Because this is WSL, Ubuntu must remain running for the server to be available. A hidden `wsl ... sleep infinity` keepalive was started during validation; it is a session process, not a Windows boot task. After a Windows reboot, launch Ubuntu again to start its enabled Ollama service.

The model had originally been downloaded under `/home/caden/.ollama/models`, while the service store was empty. The same existing files were hard-linked into `/usr/share/ollama/.ollama/models` on the same Linux filesystem; the new service-store directories were assigned to the `ollama` user. This reused the exact downloaded bytes without a model pull or another full copy.

Raw Windows request and `/v1/models` result:
```powershell
curl.exe -fsS http://localhost:11434/v1/models
```

```json
{"object":"list","data":[{"id":"devstral-small-2:latest","object":"model","created":1791923896,"owned_by":"library"}]}
```

The agent then issued an actual generation request from Windows PowerShell:
```powershell
$requestBody = @{
    model = 'devstral-small-2'
    messages = @(@{role='user'; content='hi'})
    temperature = 0
    max_tokens = 32
} | ConvertTo-Json -Depth 6 -Compress
Invoke-RestMethod -Uri 'http://localhost:11434/v1/chat/completions' -Method Post -ContentType 'application/json' -Body $requestBody
```

Raw JSON response (serialized with `ConvertTo-Json -Depth 8 -Compress`):
```json
{"id":"chatcmpl-89","object":"chat.completion","created":1792443932,"model":"devstral-small-2","system_fingerprint":"fp_ollama","choices":[{"index":0,"message":{"role":"assistant","content":"Hello! How can I assist you today?"},"finish_reason":"stop"}],"usage":{"prompt_tokens":556,"prompt_tokens_details":{"cached_tokens":554},"completion_tokens":10,"total_tokens":566}}
```

Raw `ollama ps` and `/api/ps` immediately after that `/v1` request:
```text
NAME                       ID              SIZE     PROCESSOR    CONTEXT    RUNNER      UNTIL
devstral-small-2:latest    24277f07f62d    17 GB    100% GPU     32768      llamacpp    24 hours from now
{"models":[{"name":"devstral-small-2:latest","model":"devstral-small-2:latest","size":17130061823,"digest":"24277f07f62db8f9cb68e9dfc679ea1818a7fbac47a50eff0a701d3f645b63c8","details":{"parent_model":"","format":"gguf","family":"mistral3","families":["mistral3"],"parameter_size":"24.0B","quantization_level":"Q4_K_M"},"expires_at":"2026-10-20T16:05:32.489690974-05:00","size_vram":17130061823,"context_length":32768,"runner":"llamacpp"}]}
```

`size_vram` equals the reported loaded model size, context is 32768, and `expires_at` is approximately 24 hours after the request. This verification uses the OpenAI-compatible endpoint rather than relying only on a CLI generation.

Raw effective runner log excerpts:
```text
Oct 19 15:54:23 TurtleDesktop ollama[1421]: load_tensors: offloaded 41/41 layers to GPU
Oct 19 15:54:34 TurtleDesktop ollama[1421]: llama_context: n_ctx                 = 32768
Oct 19 15:54:34 TurtleDesktop ollama[1421]: llama_context: flash_attn            = enabled
Oct 19 15:54:34 TurtleDesktop ollama[1421]: llama_kv_cache:      CUDA0 KV buffer size =  2720.00 MiB
Oct 19 15:54:34 TurtleDesktop ollama[1421]: llama_kv_cache: size = 2720.00 MiB ( 32768 cells,  40 layers,  1/1 seqs), K (q8_0): 1360.00 MiB, V (q8_0): 1360.00 MiB
```

The q8_0 K and V buffers are 1360 MiB each, 2720 MiB total. Ollama's documented configuration mechanism and cache options are described in its [FAQ](https://docs.ollama.com/faq).

## Task 4 — Warm generation speed

The operator ran the exact command twice consecutively in Ubuntu:
```bash
ollama run devstral-small-2 --verbose "Write a 300-word explanation of Python's GIL."
ollama run devstral-small-2 --verbose "Write a 300-word explanation of Python's GIL."
```

Equivalent invocation from PowerShell: `wsl -d Ubuntu -u caden --exec ollama run devstral-small-2 --verbose "Write a 300-word explanation of Python's GIL."`.

Raw statistics supplied by the operator from the **second run**:
```text
total duration:       9.105700161s
load duration:        876.206µs
prompt eval count:    569 token(s)
prompt eval cached:   568 token(s)
prompt eval duration: 21.647ms
prompt eval rate:     46.20 tokens/s
eval count:           461 token(s)
eval duration:        9.079696s
eval rate:            50.77 tokens/s
```

Generation passes the requested roughly 40 tokens/s threshold. Prompt processing was almost entirely cached (568 of 569 tokens), so the 46.20 prompt rate does not establish uncached prefill throughput. The high-hundreds prompt-processing target remains unmeasured for an uncached prompt. This short warm test also does not establish total runtime for 200 agent runs with varying prompt lengths, tool calls, and Docker work.

## Task 5 — Exact model build

Pin these values in future experiment metadata without changing the model:

- Model: `devstral-small-2:latest` / Devstral Small 2 24B Instruct 2512.
- Full model/manifest digest: `24277f07f62db8f9cb68e9dfc679ea1818a7fbac47a50eff0a701d3f645b63c8`.
- CLI ID prefix: `24277f07f62d`.
- Format/architecture: GGUF / `mistral3`; parameter size: `24.0B`; quantization: `Q4_K_M`.
- Active experiment context: `32768`; model-supported maximum: `393216`.
- Model blob in the Modelfile: `c580819bed79c92d01a42227bd6d8fd66b9ec60d5329f5eb73f812f156af7807`. This blob hash is distinct from the model/manifest digest above.

The built-in Modelfile temperature remains 0.15. The experiment's `/v1` request explicitly uses temperature 0; the CLI speed test used the unchanged built-in setting.

Raw `ollama list`:
```text
NAME                       ID              SIZE     MODIFIED
devstral-small-2:latest    24277f07f62d    15 GB    6 days ago
```

Raw `ollama show devstral-small-2`:
```text
  Model
    architecture        mistral3
    parameters          24.0B
    context length      393216
    embedding length    5120
    quantization        Q4_K_M

  Capabilities
    completion
    vision
    tools

  Parameters
    temperature    0.15
```

Raw `GET /api/tags`:
```json
{"models":[{"name":"devstral-small-2:latest","model":"devstral-small-2:latest","modified_at":"2026-10-13T15:38:16.739233734-05:00","size":15177374099,"digest":"24277f07f62db8f9cb68e9dfc679ea1818a7fbac47a50eff0a701d3f645b63c8","details":{"parent_model":"","format":"gguf","family":"mistral3","families":["mistral3"],"parameter_size":"24.0B","quantization_level":"Q4_K_M","context_length":393216,"embedding_length":5120,"runner":"ggml"},"capabilities":["completion","vision","tools"]}]}
```

Raw `ollama show devstral-small-2 --modelfile` (the template below is captured model data):
```text
# Modelfile generated by "ollama show"
# To build a new Modelfile based on this, replace FROM with:
# FROM devstral-small-2:latest

FROM /usr/share/ollama/.ollama/models/blobs/sha256-c580819bed79c92d01a42227bd6d8fd66b9ec60d5329f5eb73f812f156af7807
TEMPLATE """{{- $lastUserIndex := -1 }}
{{- $hasSystemPrompt := false }}
{{- range $index, $_ := .Messages }}
{{- if eq .Role "user" }}{{ $lastUserIndex = $index }}{{ end }}
{{- if eq .Role "system" }}{{ $hasSystemPrompt = true }}{{ end }}
{{- end }}
{{- if not $hasSystemPrompt }}[SYSTEM_PROMPT]You are Devstral-Small-2-24B-Instruct-2512, a Large Language Model (LLM) created by Mistral AI, a French startup headquartered in Paris.
You power an AI assistant called Le Chat.
Your knowledge base was last updated on 2023-10-01.
The current date is {{ currentDate }}.

When you're not sure about some information or when the user's request requires up-to-date or specific data, you must use the available tools to fetch the information. Do not hesitate to use tools whenever they can provide a more accurate or complete response. If no relevant tools are available, then clearly state that you don't have the information and avoid making up anything.
If the user's question is not clear, ambiguous, or does not provide enough context for you to accurately answer the question, you do not try to answer it right away and you rather ask the user to clarify their request (e.g. "What are some good restaurants around me?" => "Where are you?" or "When is the next flight to Tokyo" => "Where do you travel from?").
You are always very attentive to dates, in particular you try to resolve dates (e.g. "yesterday" is {{ yesterdayDate }}) and when asked about information at specific dates, you discard information that is at another date.
You follow these instructions in all languages, and always respond to the user in the language they use or request.
Next sections describe the capabilities that you have.

# WEB BROWSING INSTRUCTIONS

You cannot perform any web search or access internet to open URLs, links etc. If it seems like the user is expecting you to do so, you clarify the situation and ask the user to copy paste the text directly in the chat.

# MULTI-MODAL INSTRUCTIONS

You have the ability to read images, but you cannot generate images. You also cannot transcribe audio files or videos.
You cannot read nor transcribe audio files or videos.

# TOOL CALLING INSTRUCTIONS

You may have access to tools that you can use to fetch information or perform actions. You must use these tools in the following situations:

1. When the request requires up-to-date information.
2. When the request requires specific data that you do not have in your knowledge base.
3. When the request involves actions that you cannot perform without tools.

Always prioritize using tools to provide the most accurate and helpful response. If tools are not available, inform the user that you cannot perform the requested action at the moment.[/SYSTEM_PROMPT]
{{- end }}
{{- range $index, $_ := .Messages }}
{{- if eq .Role "system" }}[SYSTEM_PROMPT]{{ .Content }}[/SYSTEM_PROMPT]
{{- else if eq .Role "user" }}
{{- if and (eq $lastUserIndex $index) $.Tools }}[AVAILABLE_TOOLS]{{ $.Tools }}[/AVAILABLE_TOOLS]
{{- end }}[INST]{{ .Content }}[/INST]
{{- else if eq .Role "assistant" }}
{{- if .Content }}{{ .Content }}
{{- if and (not .ToolCalls) (not (eq (len (slice $.Messages $index)) 1)) }}</s>
{{- end }}
{{- end }}
{{- if .ToolCalls }}[TOOL_CALLS]
{{- range .ToolCalls }}{{ .Function.Name }}[ARGS]{{ .Function.Arguments }}
{{- end }}</s>
{{- end }}
{{- else if eq .Role "tool" }}[TOOL_RESULTS]{{ .Content }}[/TOOL_RESULTS]
{{- end }}
{{- end }}"""
PARAMETER temperature 0.15
```

## Task 6 — Docker validation

Raw `docker version`:
```text
Client:
 Version:           28.1.1
 API version:       1.49
 Go version:        go1.23.8
 Git commit:        4eba377
 Built:             Fri Apr 18 09:53:24 2025
 OS/Arch:           windows/amd64
 Context:           desktop-linux

Server: Docker Desktop 4.41.2 (191736)
 Engine:
  Version:          28.1.1
  API version:      1.49 (minimum version 1.24)
  Go version:       go1.23.8
  Git commit:       01f442b
  Built:            Fri Apr 18 09:52:57 2025
  OS/Arch:          linux/amd64
  Experimental:     false
 containerd:
  Version:          1.7.27
  GitCommit:        05044ec0a9a75232cad458027ca83437aae3f4da
 runc:
  Version:          1.2.5
  GitCommit:        v1.2.5-0-g59923ef
 docker-init:
  Version:          0.19.0
  GitCommit:        de40ad0
```

Raw `docker info --format "{{.OSType}} {{.Architecture}} {{.DockerRootDir}}"`:
```text
linux x86_64 /var/lib/docker
```

Raw operator-supplied `docker run --rm hello-world` output:
```text
Hello from Docker!
This message shows that your installation appears to be working correctly.

To generate this message, Docker took the following steps:
 1. The Docker client contacted the Docker daemon.
 2. The Docker daemon pulled the "hello-world" image from the Docker Hub.
    (amd64)
 3. The Docker daemon created a new container from that image which runs the
    executable that produces the output you are currently reading.
 4. The Docker daemon streamed that output to the Docker client, which sent it
    to your terminal.

To try something more ambitious, you can run an Ubuntu container with:
 $ docker run -it ubuntu bash

Share images, automate workflows, and more with a free Docker ID:
 https://hub.docker.com/

For more examples and ideas, visit:
 https://docs.docker.com/get-started/
```

### Static arm: read-only Windows mount

The operator's first `-v` attempt downloaded Alpine but returned:
```text
cat: can't open '/testbed/a.txt': No such file or directory
```

The agent verified that the Windows file existed and read `hi`. An explicit `--mount` using `C:/temp/mounttest` then succeeded. The initial failure's cause was not conclusively isolated.

Successful final command:
```powershell
docker run --rm --mount 'type=bind,source=C:/temp/mounttest,target=/testbed,readonly' alpine sh -c 'cat /testbed/a.txt && (touch /testbed/b 2>&1 || true)'
```

Raw output (exit 0; the write rejection is expected):
```text
hi
touch: /testbed/b: Read-only file system
```

### Execution arm: official SWE-bench image

Read the installed source at `.venv\Lib\site-packages\minisweagent\run\benchmarks\swebench.py`, lines 68–76. Raw function:
```python
def get_swebench_docker_image_name(instance: dict) -> str:
    """Get the image name for a SWEBench instance."""
    image_name = instance.get("image_name", None) or instance.get("docker_image", None)
    if image_name is None:
        # Docker doesn't allow double underscore, so we replace them with a magic token
        iid = instance["instance_id"]
        id_docker_compatible = iid.replace("__", "_1776_")
        image_name = f"docker.io/swebench/sweb.eval.x86_64.{id_docker_compatible}:latest".lower()
    return image_name
```

Computed by calling that installed helper with `{'instance_id': 'astropy__astropy-13579'}`:
```text
docker.io/swebench/sweb.eval.x86_64.astropy_1776_astropy-13579:latest
```

Raw `docker pull` output:
```text
latest: Pulling from swebench/sweb.eval.x86_64.astropy_1776_astropy-13579
39a945af8df2: Pulling fs layer
d92231e62f82: Pulling fs layer
5a85c8793cb6: Pulling fs layer
4f4fb700ef54: Pulling fs layer
fd3f01088aa8: Pulling fs layer
1555dd6c7db6: Pulling fs layer
079027039e9d: Pulling fs layer
407de719c2d3: Pulling fs layer
0ddda5039dab: Pulling fs layer
53752545c9b2: Pulling fs layer
53752545c9b2: Download complete
5a85c8793cb6: Download complete
4f4fb700ef54: Download complete
407de719c2d3: Download complete
0ddda5039dab: Download complete
39a945af8df2: Download complete
39a945af8df2: Pull complete
d92231e62f82: Download complete
1555dd6c7db6: Download complete
fd3f01088aa8: Download complete
fd3f01088aa8: Pull complete
079027039e9d: Download complete
53752545c9b2: Pull complete
407de719c2d3: Pull complete
0ddda5039dab: Pull complete
079027039e9d: Pull complete
5a85c8793cb6: Pull complete
1555dd6c7db6: Pull complete
4f4fb700ef54: Pull complete
d92231e62f82: Pull complete
Digest: sha256:f3dc11a37649430d6a7a3aac4858477db8f19d9a941f056f875e8e566066ffe3
Status: Downloaded newer image for swebench/sweb.eval.x86_64.astropy_1776_astropy-13579:latest
docker.io/swebench/sweb.eval.x86_64.astropy_1776_astropy-13579:latest
```

Measured with a PowerShell `Stopwatch` around the complete `docker pull` process:
```text
Pull exit code: 0
Pull elapsed seconds: 37.029
```

Validation commands:
```powershell
docker run --rm docker.io/swebench/sweb.eval.x86_64.astropy_1776_astropy-13579:latest python --version
docker image inspect docker.io/swebench/sweb.eval.x86_64.astropy_1776_astropy-13579:latest --format 'Id={{.Id}} SizeBytes={{.Size}} RepoDigests={{json .RepoDigests}}'
```

Raw output:
```text
Python 3.11.5
Id=sha256:f3dc11a37649430d6a7a3aac4858477db8f19d9a941f056f875e8e566066ffe3 SizeBytes=1134312546 RepoDigests=["swebench/sweb.eval.x86_64.astropy_1776_astropy-13579@sha256:f3dc11a37649430d6a7a3aac4858477db8f19d9a941f056f875e8e566066ffe3"]
```

Docker reports an image size of 1,134,312,546 bytes (about 1.06 GiB). Pin the `RepoDigests` value above if the exact image needs to be reused, rather than depending only on `latest`.

### Docker storage capacity

`/var/lib/docker` is inside Docker Desktop's WSL disk. Its Windows backing file is on `C:`, not the large `D:` drive used by Ubuntu. Raw backing-file size and free-space output after the pull:
```text

FullName : C:\Users\caden\AppData\Local\Docker\wsl\disk\docker_data.vhdx
Length   : 5629804544


Name     FreeBytes FreeGiB
----     --------- -------
C      96152829952   89.55
D    5653505409024 5265.24
```

The `docker_data.vhdx` file occupies 5,629,804,544 bytes by file length; this includes Docker storage beyond the single reported image size. `C:` had 89.55 GiB free after validation. A full 100-instance/200-run experiment still needs an image retention or storage plan; one successful image pull does not show that all instance images can coexist on `C:`.

## Handoff

The generation-speed gate is met at 50.77 tokens/s, all 25 native Windows tests pass, and both requested Docker environment checks pass. The serving settings, exact model build, raw command outputs, initial failures, and measurement limitations are recorded above. The uncached prefill target and isolated effects of each memory change were not measured. No M3–M5 runs or full 200-run experiment were launched as part of this machine validation.

Branch publication commands:
```powershell
git add docs/serving.md
git commit -m "4090 serving and validation results"
git push -u origin serving/4090
```
