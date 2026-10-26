# Does Code Execution Help LLM Agents Localize Bugs? A Progress Report

*CS527 Project — October 26, 2026*

## 1. Motivation and Research Question

Fault localization is the task of identifying the source-code locations responsible for a reported software defect. It is an important step in debugging: before developers can repair a problem, they need to narrow a potentially large repository to relevant files, functions, and lines. An LLM agent can assist by interpreting a bug report, searching the repository, and examining candidate implementations. An agent with execution access can additionally run tests or small programs and use their outputs to investigate its hypotheses.

Our project asks: **Does allowing code execution improve an LLM agent’s ability to localize bugs compared with allowing only source-code reading and searching?** We also aim to examine whether any improvement justifies the additional time, tokens, and agent steps required. Execution could provide useful evidence, but it could also consume resources without identifying a better location.

We are building a reproducible experiment around an existing agent framework and an open-weight model. The task is localization only: the agent will return ranked suspected locations rather than produce a repair. This scope makes localization accuracy the direct outcome of interest and keeps the comparison focused on the contribution of execution access.

## 2. Experimental Design

The experiment uses a frozen sample of **100 SWE-bench Verified instances**. Sampling is seeded and stratified by repository, allocating at least three instances per repository where available and distributing the remaining slots proportionally. Each instance records its repository and base commit, allowing both experimental conditions to investigate the same source version. Running every instance under both conditions will produce 200 planned agent runs.

Both conditions will use mini-SWE-agent and Devstral Small 2. The experimental design holds the model, system prompt, temperature, step limit, command timeout, and output truncation settings constant. The primary difference is the environment available to the agent:

- **Static arm:** The agent can read and search source code inside a generic container containing shell utilities and Git. The repository is mounted read-only, and the image excludes Python and project dependencies. The checkout contains the required base commit without later repository history.
- **Execution arm:** The agent uses the official SWE-bench container for the instance, providing the repository and its execution environment. It can inspect code and run Python, tests, or other supported commands to gather additional evidence.

The agent will finish by returning up to five ranked locations in a structured JSON response. Each location includes a file path, an optional qualified function name, and a line range. Reference locations are extracted from the benchmark’s gold patch against the base commit, excluding test files.

Accuracy is measured using **Hit@1, Hit@3, and Hit@5** at file, function, and line levels. A hit occurs when at least one of the first *k* predictions matches a reference location at the corresponding level. File matching compares normalized paths; function matching compares the file and qualified function name; line matching checks overlap with a reference range. Predictions are deduplicated separately at each level.

The experiment will also record input and output tokens, elapsed time, agent steps, execution attempts, and parsing failures. Failed runs will retain an explicit result row. Comparing both arms on the same instances will support analysis of accuracy differences alongside their computational costs.

## 3. Work Completed

The current work establishes the data, evaluation, and serving foundations for the experiment. The repository contains a Python package and a unified `execloc` command-line interface for environment checking, sampling, gold-location extraction, running experiments, and scoring. The interface is present, although environment checking and experiment execution still require implementation. Python and dependency versions are pinned through `.python-version`, `pyproject.toml`, and `uv.lock` to support consistent setup across teammates’ machines.

### Dataset and reference locations

The sample and reference data are committed as `data/instances.jsonl` and `data/gold.jsonl`, each containing 100 records. These files are frozen so subsequent development and experiments use the same evaluation set. The preparation commands guard against accidental overwriting, and JSONL output uses consistent UTF-8 encoding and line endings across platforms.

Gold-location extraction is implemented using patch parsing and Python’s abstract syntax tree. Modified or deleted lines are mapped to their positions in the original source and to enclosing functions where applicable. Function names preserve class and nesting information. For changes that only insert lines, the implementation uses the insertion boundary for line scoring and identifies the enclosing function when possible. Module-level changes do not receive an artificial function label, while newly created files contribute file-level reference locations.

### Scoring and verification

The scoring module produces per-instance metrics and a summary for each experimental arm. It normalizes paths, handles repeated predictions, and computes the nine combinations of localization level and ranking cutoff. Missing predictions within a represented arm count as misses. When an instance has no reference item at a particular level, it is excluded from that level’s average rather than assigned a misleading score. Summary output also includes available resource-use measurements and parsing-error rates.

The recorded Windows validation on October 19 reports **25 passing tests**, covering gold-location extraction and scoring. These tests provide evidence that the implemented evaluation rules behave as intended on their test cases. Experimental prediction files have not yet been produced, so there are currently no measured localization accuracy results.

### Model serving and container validation

The serving machine has an RTX 4090 with 24 GB of GPU memory, an Intel i7-14700K, and 32 GB of system RAM. The project runs natively on Windows, while Ollama serves the model from Ubuntu under WSL2. Validation identified the model as the 24-billion-parameter Devstral Small 2 using Q4_K_M quantization and recorded its exact digest.

After memory configuration changes, the loaded model reported full GPU residency and an active context length of **32,768 tokens**. The documented warm generation measurement reached **50.77 tokens per second**. This establishes a working serving configuration; the measurement is generation throughput rather than end-to-end experiment speed. Requests through the OpenAI-compatible interface were also validated.

Docker checks confirmed that a Windows directory could be mounted read-only and that an official Astropy SWE-bench image could run Python successfully. These checks establish the basic container capabilities required by the two arms. Detailed outputs and configuration evidence are preserved in [the serving validation record](docs/serving.md).

| Component | Current status |
|---|---|
| Package, CLI skeleton, and dependency lock | Implemented |
| Frozen sample and gold locations | Complete: 100 records each |
| Scoring and evaluation tests | Implemented; 25 tests passed in recorded Windows validation |
| Local model serving and Docker checks | Validated on the RTX 4090 machine |
| Environment checker, response parser, and experiment runner | Pending |
| Batch execution, resume support, and full evaluation | Pending |

## 4. Problems to Be Fixed

The immediate priority is implementing the environment checker, response parser, and runner so the existing components form a complete workflow. Both arm configurations must enforce the agreed settings and capture consistent prediction and resource-use records. Docker image storage also needs an operational solution: validation recorded approximately 89.55 GiB free on the drive holding Docker data, making on-demand image pulling and optional cleanup relevant for the full experiment. The README needs a concise quickstart once a complete single-instance run works.

## 5. Next Steps and Expected Contribution

We will first complete environment checks for Docker availability, endpoint connectivity, model availability, and storage. We will then implement and validate one static run and one execution-enabled run on the same instance. This stage will verify that the static environment prevents Python execution, that the execution environment supports it, and that both runs produce parseable ranked locations and complete metadata.

Next, we will add batch processing and resume support. Completed instance-arm pairs must be skipped when a run restarts, while crashes and malformed responses must remain visible in the output. A five-instance pilot in both arms will check response formatting, context usage, execution behavior, and resource requirements before the complete experiment. Any necessary configuration adjustments will be settled before the full comparison.

After running all 100 instances under both conditions, we will compare localization accuracy at each granularity and ranking cutoff, together with time, token use, and agent steps. We plan to examine cases where the arms disagree to understand how execution evidence helped or failed to help the agent. Repository-level summaries can provide additional context for the aggregate results.

The expected contribution is an empirical assessment of execution access for agentic fault localization under a fixed model and agent setup. The frozen dataset, explicit scoring rules, recorded configurations, and retained run outputs will make the comparison reproducible and support later investigation of the relationship between localization quality and computational cost.
