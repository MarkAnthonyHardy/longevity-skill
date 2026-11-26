# Longevity

A skill for Claude (and other AIs) that keeps a job inside your plan's usage limit and the 200K-token task limit. It picks a model that will fit, checks the real usage meter as it works, and starts a fresh session on its own before either limit runs out, so you are not left waiting for a refill.

## What it does

- Chooses a model for the job (or uses the one you name), then checks whether the job will fit in the remaining allowance.
- Reads your real usage meter: the plan window (weekly, and a 5-hour or per-model window if you have one) and this task's token count.
- Works in GREEN, AMBER, or RED. In AMBER it uses a cheaper model for routine steps and does less parallel work. In RED it finishes the current step only, then hands over.
- Writes a `CONTINUE_HERE.md` handover note to `~/.claude/handovers/<job>/` (a scratch folder outside your repo, so it never clashes with a "no repo edits" rule) and starts the next session itself, so a large job can continue across sessions until it is done.
- For any job that is not small, starts with a cheap read-only dry run (`DRYRUN.md`) and sizes the job from real past token counts when they exist (`pace.py --history`).
- Runs several jobs in parallel safely: a coordinator guide sizes them against the one shared allowance, and a fixed worker block is pasted into every brief so each worker checks its own tokens and hands over on its own.
- It never switches on extra or pay-as-you-go usage unless you have already granted a cap for that purpose.

## Install

Copy the skill and the tools into your Claude folders:

```bash
mkdir -p ~/.claude/skills/longevity ~/.claude/tools
cp SKILL.md COORDINATOR.md WORKER_BLOCK.md DRYRUN.md PACE.md ZONES.md HANDOVER.md ~/.claude/skills/longevity/
cp tools/pace.py tools/model-costs.json ~/.claude/tools/
chmod +x ~/.claude/tools/pace.py
```

In Claude, type `/longevity` at the start of a job, or say "use the Longevity skill".

You can also run the meter tool yourself:

```bash
python3 ~/.claude/tools/pace.py --used 72 --resets-at "2026-12-01 00:00" --window-hours 168
```

## Example

You start a large coding job. Longevity checks your meter, sees plenty of allowance, and works normally on Sonnet. After several steps the task is close to 160K tokens, so it finishes the current step, writes `CONTINUE_HERE.md` with the goal, what "done" looks like, the model to use, and the next command, then opens a new session with that file as the first message. The new session picks up where the last one stopped, and the job keeps going.

If the allowance itself is almost gone, it will suggest a cheaper model for the leftover work, or tell you the earliest refill time and stop, rather than running the meter dry.

## Several jobs at once

All sessions draw on one allowance. Size them from history when you have it, then see each worker's share:

```bash
python3 ~/.claude/tools/pace.py --history review
python3 ~/.claude/tools/pace.py --model sonnet --usage-json usage.json --estimate 10 --sessions 4
```

`--history` reports the median and high final tokens for past jobs of that type (or says there is no history). If the high is 160K or more, split the job before starting. `--sessions` splits the remaining allowance (minus a 5% reserve) across N workers. A worker logs a real final count with:

```bash
python3 ~/.claude/tools/pace.py --tokens 150000 --worker my-job --job-type review --final
```

Details live one level down from `SKILL.md`: `ZONES.md`, `HANDOVER.md`, `DRYRUN.md`, `COORDINATOR.md`, `WORKER_BLOCK.md` and `PACE.md` (loaded only when needed).
