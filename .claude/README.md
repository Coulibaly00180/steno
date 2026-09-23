# Claude Code subagents for Sténo

Project-scoped subagents are defined in `.claude/agents/`.

Included agents:

- `backend-engineer` — FastAPI, SQLAlchemy, PostgreSQL, Redis/RQ, SSE and backend API.
- `frontend-engineer` — Next.js/React/TypeScript and browser UX.
- `ai-pipeline-engineer` — FFmpeg, faster-whisper, Ollama, local LLMs and long-video processing.
- `test-engineer` — regression, API, worker, Compose and E2E validation.
- `security-reviewer` — read-only security review.
- `docker-devops` — Docker/Compose, CPU/GPU, CI and Windows development.

## Usage

Restart Claude Code after adding a brand-new `.claude/agents/` directory so it discovers the files.

Claude may choose these automatically based on each `description`, or you can request one explicitly, for example:

```text
Use the ai-pipeline-engineer subagent to design and implement pyannote diarization without breaking CPU mode.
```

```text
Ask backend-engineer and frontend-engineer to analyze this feature in parallel, then implement the agreed API and UI.
```

```text
Use test-engineer to build a regression test plan and run the relevant checks for my current changes.
```

```text
Use security-reviewer to review the current diff. Do not modify anything; return only actionable findings.
```

For broad features, let the main Claude session orchestrate multiple agents and keep the final integration decision in the main context.
