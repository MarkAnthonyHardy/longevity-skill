---
name: longevity
description: Keeps AI work inside the plan's usage allowance and the 200K-token task limit by reading the real meters, sizing jobs, and handing over to a fresh session before a limit is hit. Use when the user mentions usage limit, allowance, quota, refill, tokens, 200K, context size, handover, or a long, large or parallel job; when starting any job that is not small (dry run); when coordinating several workers; or on any limit warning.
---

# Longevity

Stay inside two limits: the plan's allowance until it refills, and 200K tokens of
context per task. Read the real meter. Never guess a reading or claim one you did
not take. Tool: `python3 ~/.claude/tools/pace.py` (PACE).

## Core workflow
1. **Size the job.** `PACE --history <job-type>` (or a rough percent on sonnet).
   If the expected size or historical high is 160K tokens or more, split it into
   numbered sub-tasks that each fit and do sub-task 1 only.
2. **Dry run** unless the job is small (under about 50K). Read-only plan on
   sonnet or haiku, written to `~/.claude/handovers/<job>/DRYRUN.md`. Work starts from it.
3. **Read the real meter** at start, after each step, and before any large read,
   test run, subagent, or step adding more than about 20K tokens: call
   `mcp__ccd_session_mgmt__get_usage` and use `context.tokensUsed`. Never type a
   guessed count.
4. **Check the zone.** `PACE --tokens <tokensUsed>`:
   - OK under 160K. Carry on.
   - 160K–199K: finish the current step only, then hand over.
   - 200K or more: stop after the current tool call and hand over.
   - A guessed count (`--estimated`) uses 130K / 170K instead.
5. **Hand over** automatically, without waiting for the user.
6. **Report** one line: model, allowance zone and percent, tokens, reset time,
   where the work went. Then stop. On finish or handover, log the real final:
   `PACE --tokens <n> --worker <job> --job-type <type> --final`.

## Read only when needed
| When | Read |
|---|---|
| Choosing a model, allowance zones GREEN/AMBER/RED, non-Claude AIs | [ZONES.md](ZONES.md) |
| A job is not small, or you are writing a dry run | [DRYRUN.md](DRYRUN.md) |
| A token zone says hand over, or you receive a handover | [HANDOVER.md](HANDOVER.md) |
| Starting several jobs or workers, or reporting a worker's state | [COORDINATOR.md](COORDINATOR.md) |
| Writing a worker brief | [WORKER_BLOCK.md](WORKER_BLOCK.md) |
| Unsure of a PACE flag | [PACE.md](PACE.md) |

All sessions share one allowance. Never switch on extra or pay-as-you-go usage yourself.
