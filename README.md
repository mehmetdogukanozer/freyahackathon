# Chillers: a builder that builds Freya phone agents

Team **Chillers** (Şehri Sena Demirbaş, Mehmet Doğukan Özer) at the **Build the Builder** hackathon by [Freya](https://www.linkedin.com/company/freyavoice/) (YC S25), sponsored by Y Combinator.
We finished **10th/11th on the leaderboard with 70 points**, tied with another team.

## What this is

A **builder** is a program that reads a bank's brief and creates a phone agent (a Freya workflow in JSON) with no manual step.
The phone agent then talks to simulated customers, and a scorer checks the bank's records and the transcript.
There is no partial credit, and every customer is run three times: a customer counts only if all three runs pass.

Our builder is a Claude Agent SDK session (`examples/agent_builder.py`) that follows one skill
(`.claude/skills/build-phone-agent/SKILL.md`). Nothing from a particular brief is written into the skill or the script.

## The final run

The final case was a folder of raw bank material (mails, meeting transcript, service contract, IVR sheet, FAQ, test customers), in Turkish, about credit card statement debt.
One run of the builder, 21 minutes (limit: 30), about $6 of model cost:

- read every source and wrote down which newer document overrides which older one,
- wrote a rules file, then a generator script, then the workflow (44 nodes, 40 tools),
- checked the mock bank against the pack's answers (616 answers), ran the agent's code locally, then ran its own Turkish customers on the platform and fixed what failed.

Evidence: `traces/final.log` (plain text), `traces/20261011-210044.jsonl` (full trace), `submission/final.workflow.json` (the exported phone agent), `submission.json`.
`final-mock/` is a small mock of the bank's statement service that came with the final case; it is not part of the builder.

## What the skill teaches the builder

Lessons from our own failed runs, written as rules:

1. **Rules in code, not in prompts.** Counters ("ask at most twice"), dates and holiday arithmetic, amounts and fixed sentences live in code steps. The phone agent's model forgets counts under pressure.
2. **A cut-off message is not an answer.** Simulated customers are often cut off mid-sentence. A fragment is not a refusal and not an attempt; ask again neutrally.
3. **Special situations are routed first**: already paid, dispute, hardship, human request, scam question, wrong person.
4. **Handle lists.** A customer can have several products or cards. State per item.
5. **Read everything, trust the newest source**, and write each conflict and its winner in the rules file.
6. **Test with your own personas**, including angry, silent and prompt-injection customers, and never edit a test to make it pass.
7. **Watch the clock**: save a first working version by minute 12, stop changing at minute 25.

## What did not work as well

- Customers that talk a lot (`talkative` style) were our weakest case in practice runs.
- Some judge verdicts came back "fail" with no reason, so we could not tell a real bug from a strict judge.
- Brief A to Brief B practice: our early builder merged two briefs into one agent, which broke a rule one brief forbids. The skill now says one agent and one slot per brief.

## Run it

```bash
cp env.example .env          # FREYA_API_KEY and FREYA_BANK_TEAM_KEY (never commit .env)
pip install claude-agent-sdk
python3 examples/agent_builder.py {case_folder} {mock_url}
```

Model and limit: `BUILDER_MODEL`, `BUILDER_TIME_LIMIT_S` (default 28 minutes). Our final run used `claude-opus-5-5`.
More in `BUILDER.md`. The original starter kit's README is kept in `docs/KIT_README.md`.

## Credits

The kit (`freya.py`, `docs/`, `tools/`, `examples/`, practice briefs) is from Freya's starter kit. The builder skill, the final run and its trace are our work.
All data is synthetic.
