"""A small agentic builder: Claude reads the brief, writes the phone agent, tests it and fixes it.

    pip install claude-agent-sdk
    python3 examples/agent_builder.py briefs/brief-a.md [briefs/brief-a.openapi.json]

Arguments: one brief file (.md or any text) and, optionally, its tool API file (.json). Several briefs are allowed;
a .json argument is always read as a tool API file. The slot of a brief is its file name without the extension.

It runs Claude through the Claude Agent SDK with one skill, `.claude/skills/build-phone-agent`.
The skill is the builder's knowledge. Improve the skill and you improve every agent it builds.
Needs FREYA_API_KEY and FREYA_BANK_TEAM_KEY (see env.example, or a .env file next to this kit) and a Claude login or ANTHROPIC_API_KEY.

What a run leaves behind:
- traces/<start time>.jsonl : every message of the run (the full trace).
- traces/<slot>.log         : the same run as plain text: model calls, tool calls, what was created, tests, start and end.
- submission/<slot>.workflow.json : the saved phone agent, exported at the end (this is what is submitted).
Secrets (the API key, the team key, anything that looks like a key) are replaced by [REDACTED] in the trace files.

Safety and hygiene added to the starter:
- start_clean(): files of an earlier run (out/, results/) are moved to archive/, so the builder cannot reuse them.
- A hard time limit (BUILDER_TIME_LIMIT_S, default 28 minutes). The contest allows 30 minutes per brief.
- The trace ends with an "end" line (end time, status, seconds).
"""
import asyncio, dataclasses, json, os, pathlib, re, shutil, sys, time

from claude_agent_sdk import AssistantMessage, ClaudeAgentOptions, ResultMessage, TextBlock, ToolUseBlock, query

KIT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(KIT))
TIME_LIMIT_S = int(os.environ.get("BUILDER_TIME_LIMIT_S", str(28 * 60)))
SECRET_NAMES = ("FREYA_API_KEY", "FREYA_BANK_TEAM_KEY", "ANTHROPIC_API_KEY")


def load_env():
    """Read KIT/.env into this process, so the builder's own shell inherits the keys. Keys already set are kept.
    Plain string handling on purpose: a PowerShell regex with a Turkish locale does not match the letter I."""
    f = KIT / ".env"
    if not f.exists():
        return
    for line in f.read_text(encoding="utf-8-sig").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export "):]
        name, sep, value = line.partition("=")
        name, value = name.strip(), value.strip().strip('"').strip("'")
        if sep and name and value and not os.environ.get(name):
            os.environ[name] = value


def scrub(text):
    """Replace secrets in text that goes into a trace file."""
    for name in SECRET_NAMES:
        v = os.environ.get(name)
        if v and len(v) >= 8:
            text = text.replace(v, "[REDACTED]")
    return re.sub(r"\b(fak_|sk-ant-)[A-Za-z0-9_\-]{8,}", "[REDACTED]", text)


def start_clean():
    """Move the files of an earlier run out of the way. Nothing is deleted."""
    stamp = time.strftime("%Y%m%d-%H%M%S")
    for name in ("out", "results"):
        d = KIT / name
        if d.is_dir() and any(d.iterdir()):
            dest = KIT / "archive" / stamp
            dest.mkdir(parents=True, exist_ok=True)
            shutil.move(str(d), str(dest / name))
            print(f"moved the old {name}/ to archive/{stamp}/{name}", flush=True)


def split_args(args):
    """Separate the brief files from the tool API files (.json)."""
    briefs = [a for a in args if not a.lower().endswith(".json")]
    apis = [a for a in args if a.lower().endswith(".json")]
    return briefs, apis


class Log:
    """Print to the screen and write the same line, scrubbed, to traces/<slot>.log."""
    def __init__(self, path):
        self.f = path.open("w", encoding="utf-8")

    def __call__(self, text=""):
        print(text, flush=True)
        self.f.write(scrub(str(text)) + "\n")
        self.f.flush()

    def close(self):
        self.f.close()


def export_agents(slots, out, log):
    """Export every phone agent this run saved. A failure here is logged and never stops the run."""
    try:
        import freya
        state = json.loads((KIT / ".freya-agents.json").read_text())
        (KIT / "submission").mkdir(exist_ok=True)
        for slot in slots:
            if slot in state:
                path = freya.export_workflow(state[slot]["workflow_id"], str(KIT / "submission" / f"{slot}.workflow.json"))
                wf = json.loads(pathlib.Path(path).read_text(encoding="utf-8")).get("workflow", {})
                n_nodes = len(wf.get("nodes", {}))
                n_tools = len(wf.get("tools", [])) + sum(len(n.get("tools", [])) + len(n.get("pre_actions", [])) for n in wf.get("nodes", {}).values())
                info = {"export": slot, "workflow_id": state[slot]["workflow_id"], "agent_id": state[slot].get("agent_id"), "version": state[slot].get("version"), "nodes": n_nodes, "tools": n_tools, "file": path}
                out.write(json.dumps(info) + "\n")
                log(f"exported {slot}: workflow {info['workflow_id']}, agent {info['agent_id']}, version {info['version']}, {n_nodes} nodes, {n_tools} tools, file {path}")
            else:
                out.write(json.dumps({"export": slot, "error": "no saved workflow for this slot"}) + "\n")
                log(f"no saved workflow for {slot}, nothing to export")
    except Exception as e:
        out.write(json.dumps({"export_error": scrub(str(e))[:300]}) + "\n")
        log(f"export failed: {scrub(str(e))[:300]}")


async def main(args):
    load_env()
    briefs, apis = split_args(args)
    if not briefs:
        sys.exit("give at least one brief file (and its tool API .json file)")
    missing = [k for k in ("FREYA_API_KEY", "FREYA_BANK_TEAM_KEY") if not os.environ.get(k)]
    if missing:
        sys.exit("missing: " + ", ".join(missing) + " (set them or put them in .env, see env.example)")
    slots = [pathlib.Path(b).stem for b in briefs]
    start_clean()
    started = time.time()
    stamp = time.strftime("%Y%m%d-%H%M%S")
    trace = KIT / "traces" / (stamp + ".jsonl")
    trace.parent.mkdir(exist_ok=True)
    log = Log(KIT / "traces" / ("-".join(slots) + ".log"))
    options = ClaudeAgentOptions(
        cwd=KIT,
        model=os.environ.get("BUILDER_MODEL", "claude-sonnet-5-5"),
        skills=["build-phone-agent"],          # the skill in .claude/skills
        setting_sources=["project"],           # only this folder's settings, not your own
        allowed_tools=["Read", "Write", "Edit", "Glob", "Grep", "Bash"],
        permission_mode="dontAsk",             # anything not listed above is refused
        max_turns=150,
        effort=os.environ.get("BUILDER_EFFORT") or None,   # low, medium, high, xhigh or max; unset = the model default
        # A test run takes up to 11 minutes. Let a command run that long, and keep it in the foreground:
        # the builder stops when Claude ends its turn, so a test left in the background is never read.
        env={"BASH_DEFAULT_TIMEOUT_MS": "900000", "BASH_MAX_TIMEOUT_MS": "1200000", "CLAUDE_CODE_DISABLE_BACKGROUND_TASKS": "1",
             "CLAUDE_CODE_DISABLE_AUTO_MEMORY": "1"},   # the builder must not read anybody's personal memory notes
    )
    prompt = "Use the build-phone-agent skill to build a phone agent for: " + " ".join(briefs)
    if apis:
        prompt += ". The tool API file for it: " + " ".join(apis)
    prompt += ". Write all your messages in English."
    with trace.open("w", encoding="utf-8") as out:
        head = {"start": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "briefs": briefs, "tool_api": apis, "model": options.model, "effort": options.effort, "time_limit_s": TIME_LIMIT_S}
        out.write(json.dumps(head) + "\n")
        log(f"START {head['start']}  briefs={briefs}  tool_api={apis}  model={options.model}  effort={options.effort}  limit={TIME_LIMIT_S}s")
        status = "finished"
        try:
            async with asyncio.timeout(TIME_LIMIT_S):
                async for message in query(prompt=prompt, options=options):
                    out.write(scrub(json.dumps({"type": type(message).__name__, **dataclasses.asdict(message)}, default=str)) + "\n")
                    out.flush()
                    if isinstance(message, AssistantMessage):
                        for block in message.content:
                            if isinstance(block, TextBlock):
                                log(f"[{message.model}] {block.text}")
                            elif isinstance(block, ToolUseBlock):
                                log(f"[{message.model}] tool {block.name}: {str(block.input)[:160]}")
                    elif isinstance(message, ResultMessage):
                        log(f"done: {message.num_turns} turns, {message.duration_ms / 1000:.0f} s, ${message.total_cost_usd or 0:.2f}. Trace: {trace}")
        except TimeoutError:
            status = "stopped at the time limit"
            log(f"stopped: the {TIME_LIMIT_S} s limit was reached. The last saved version of each agent stands.")
        export_agents(slots, out, log)
        end = {"end": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "status": status, "seconds": round(time.time() - started)}
        out.write(json.dumps(end) + "\n")
        log(f"END {end['end']}  status={status}  seconds={end['seconds']}")
    log.close()


if __name__ == "__main__":
    if len(sys.argv) < 2:
        sys.exit(__doc__)
    asyncio.run(main(sys.argv[1:]))
