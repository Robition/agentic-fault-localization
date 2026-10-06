# Standing rules for coding agents

See SETUP.md for the full brief and milestones.

## Hard rules

- **No paid APIs.** All inference goes to a self-hosted, OpenAI-compatible endpoint. The code must read the endpoint from `EXECLOC_BASE_URL` and fail if it's unset. Never fall back to a hosted provider.
- **Lock everything.** Use uv. Commit `pyproject.toml`, `uv.lock`, and `.python-version`. Every command in the docs starts with `uv run`.
- **Keep GPU and serving packages out of the lockfile.** Don't add vLLM, torch, or llama-cpp to the project dependencies. They break dependency resolution on macOS and Windows. Model serving is documented separately and runs outside this environment.
- **No make, no bash scripts for required workflows.** Teammates use Linux, macOS, and Windows. Expose every workflow as a subcommand of one Python CLI.
- **Verify, don't guess.** Before you use any mini-SWE-agent or SWE-bench API, read the installed package's source or docs. Don't rely on remembered flags or class names.
- **Stop and ask the human** when a section says "Decision needed," or when a check fails twice.
