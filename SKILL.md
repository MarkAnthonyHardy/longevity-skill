---
name: longevity
description: Pick the right model and pace work against the AI plan's usage limit and the 200K-token task limit, handing over to a fresh session (started automatically) before either runs out. Use at the start of any job, after each step, before agents or long runs, and on any limit warning.
---

Keep every task inside two limits: the plan's allowance until it refills, and
200K tokens of context per task. Read the real meter; never guess a reading or
claim one you did not take.

Tool: `python3 ~/.claude/tools/pace.py` (called PACE below).

## 0. Pick the model (job start)
- Use the model the user names. Otherwise: opus for hard reasoning, design and
  reviews; sonnet for normal coding; haiku for mechanical steps (formatting,
  lookups, simple edits); fable only when the user asks for it.
- Estimate the job size as a percent of the allowance on sonnet, then check it
  fits on the chosen model:
  `PACE --model <opus|sonnet|haiku|fable> --usage-json <get_usage file> --estimate <pct>`
  - FITS: go ahead.
  - TIGHT: go ahead in AMBER mode, using the SUGGEST model for routine parts.
  - DOES NOT FIT: use the cheaper SUGGEST model, or split the job (section 2).
- Relative costs live in `~/.claude/tools/model-costs.json`.

## 1. Read both meters (job start, after each finished step, before any agent or long run)
- Claude desktop app: call `mcp__ccd_session_mgmt__get_usage`. Each entry in
  `plan.windows[]` gives `percentUsed` and `resetsAt` (all-models, any per-model
  window such as Fable, and a 5-hour limit if shown); `context.tokensUsed` gives
  this task's tokens. Save the JSON to a file for PACE.
- Codex: `PACE --codex` for the allowance, `PACE --codex-context` for tokens.
  STALE means the reset has passed: do one turn, then read again.
- Any other AI has no meter it can read itself. Ask the user once for percent used
  and reset time. Count tokens as about 4 characters per token across everything
  read and written. Without an allowance answer, work in AMBER.

## 2. Work out where you stand
- Allowance: `PACE --model <name> --usage-json <file>` (or
  `PACE --used <percent> --resets-at <time> --window-hours <168 weekly, 5 for 5-hour>`).
  The headline is the tighter of the all-models window and the model's own window.
  - GREEN: projected under 85% by reset. Work normally.
  - AMBER: projected 85–99%, or under 25% left. Cheaper mode: smaller model for
    mechanical steps, no parallel agents, no repeat test suites, short replies,
    read only the file ranges you need.
  - RED: under 10% left, or projected to hit 100% before reset. Finish the
    current step only, then go to 3.
- Task tokens: `PACE --tokens <n>`
  - OK: under 160K. Carry on.
  - FINISH STEP, THEN HAND OVER: 160K–199K. Finish the current step only, start
    no new one, then go to 3.
  - HANDOVER NOW: 200K or more. Stop after the current tool call and go to 3.
- Before starting a task, estimate its size. If it will not fit in 200K, split
  it into numbered sub-tasks that each fit, and do sub-task 1 only.

## 3. Hand over to a new context window (automatic, no waiting for the user)
1. Write CONTINUE_HERE.md in the project folder with exactly these headings:
   Goal, Done when, Branch/PR links, Agreed reviewer checklist, Model to use,
   First command to run. Done when all six are filled in.
2. Choose where the next window opens:
   - Token limit hit, allowance GREEN or AMBER: a new session on the same AI.
   - Allowance RED: a new session on a model whose meter is GREEN or AMBER
     (for example opus to sonnet).
   - No AI has headroom: tell the user the earliest reset time and stop.
3. Start the new session yourself, straight away. Do not ask the user to open it.
   In Claude desktop, use its session tool (`mcp__ccd_session_mgmt__*`) to start
   a new session in the same project folder, on the model named in
   CONTINUE_HERE.md, with CONTINUE_HERE.md as its first message.
   Done when the new session has replied that it read the file.
   If you cannot start one (no session tool, or switching to a different AI
   such as Codex), tell the user in one line exactly what to open and paste.
4. The new session checks it is in the right folder (pwd) and reads
   CONTINUE_HERE.md before doing anything else. It repeats this skill, so it
   too hands over when it reaches a limit, until the job's Done when is met.
5. One worker at a time: before starting a new session, check no other session
   is already working on this job. If one is, stand down instead of starting
   another. After handing over, the old session starts no new work.
6. Pay-as-you-go or extra usage only inside a cap the user has already granted for
   that purpose. Never switch on extra usage yourself.

## 4. Report
One line: model, allowance zone and percent used, task tokens, reset time, and
where the work was handed (if anywhere). Then stop.
