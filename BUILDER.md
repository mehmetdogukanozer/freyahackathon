# The builder

`examples/agent_builder.py` opens one Claude session (Claude Agent SDK) that uses the skill
`.claude/skills/build-phone-agent/SKILL.md`. The skill reads a bank brief and its tool API file, writes a rules file,
a generator script and a workflow, saves it on Freya, tests it on its own phone agent and fixes what fails.

Run (one brief and its tool API file):

    pip install claude-agent-sdk
    export FREYA_API_KEY=...  FREYA_BANK_TEAM_KEY=...      # or put them in a .env file in this folder (see env.example)
    python3 examples/agent_builder.py {brief_path} {tool_api_path}

What a run does around the model session:
- moves the files of an earlier run (`out/`, `results/`) to `archive/`, so nothing from another brief is reused
- stops after 28 minutes (`BUILDER_TIME_LIMIT_S`), the contest allows 30
- writes `traces/<start time>.jsonl` (every message) and `traces/<slot>.log` (plain text), with keys replaced by [REDACTED]
- exports the saved phone agent to `submission/<slot>.workflow.json`

The builder model is set by `BUILDER_MODEL` (default `claude-sonnet-5-5`).
Nothing from a particular brief is written into the skill or the script.
