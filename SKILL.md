---
name: longevity
description: Pick the right model and pace work against the AI plan's usage limit and the 200K-token task limit, handing over to a fresh session (started automatically) before either runs out. Use at the start of any job, after each step, before agents or long runs, and on any limit warning.
---

Keep every task inside two limits: the plan's allowance until it refills, and
200K tokens of context per task. Read the real meter; never guess a reading or
claim one you did not take.

Tool: `python3 ~/.claude/tools/pace.py` (called PACE below). Flags: [PACE.md](PACE.md).

## 0. Pick the model (job start)
- Use the model the user names. Otherwise: opus for hard reasoning, design and
  reviews; sonnet for normal coding; haiku for mechanical steps; fable only when
  the user asks for it.
- Size the job with `PACE --history <job-type>` when past jobs of that type have
  been logged, otherwise a rough percent on sonnet, then:
  `PACE --model <opus|sonnet|haiku|fable> --usage-json <get_usage file> --estimate <pct>`
  FITS: go ahead. TIGHT: work in AMBER, using the SUGGEST model for routine parts.
  DOES NOT FIT: use the cheaper SUGGEST model, or split the job (section 3).
- If the expected size or historical high is 160K tokens or more, split into
  numbered sub-tasks that each fit, and do sub-task 1 only.
- Relative costs live in `~/.claude/tools/model-costs.json`.

## 1. Dry run (required unless the job is small)
Skip only when the job is expected under about 50K tokens. Otherwise, before any
edits, do a read-only planning pass on sonnet or haiku and write
`~/.claude/handovers/<job>/DRYRUN.md`. Real work starts from that file.
Details and the template: [DRYRUN.md](DRYRUN.md).

## 2. Read both meters
At job start, after each finished step, and before any large file read, test run,
subagent, or step expected to add more than about 20K tokens:
- Claude desktop: call `mcp__ccd_session_mgmt__get_usage`. Use `plan.windows[]`
  (`percentUsed`, `resetsAt`) and `context.tokensUsed`. Save the JSON for PACE.
  Never type a guessed token count into `--tokens`.
- Codex: `PACE --codex` and `PACE --codex-context`. STALE: do one turn, then
  read again.
- Any other AI: ask the user once for percent used and reset time; count tokens
  as about 4 characters per token. Without an allowance answer, work in AMBER.
  If you cannot read tokens, pass `PACE --tokens <n> --estimated`.

## 3. Work out where you stand
- Allowance: `PACE --model <name> --usage-json <file>` (or `--used` /
  `--resets-at` / `--window-hours` 168 weekly, 5 for a 5-hour window). The
  headline is the tighter of the all-models window and the model's own window.
  - GREEN: projected under 85% by reset. Work normally.
  - AMBER: projected 85–99%, or under 25% left. Cheaper model for mechanical
    steps, no parallel agents, no repeat test suites, short replies, read only
    the file ranges you need.
  - RED: under 10% left, or projected to hit 100% before reset. Finish the
    current step only, then go to 4.
- Task tokens: `PACE --tokens <context.tokensUsed>`
  - OK: under 160K. Carry on.
  - FINISH STEP, THEN HAND OVER: 160K–199K. Finish the current step only, then
    go to 4.
  - HANDOVER NOW: 200K or more. Stop after the current tool call and go to 4.
  - If the count is only estimated (`--estimated`): treat thresholds as 130K
    and 170K.

## 4. Hand over to a new context window (automatic, no waiting for the user)
1. Write notes to `~/.claude/handovers/<job>/CONTINUE_HERE.md` (a scratch folder
   outside the repo, so a "no repo edits" brief never blocks it). Headings:
   Goal, Done when, Branch/PR links, Agreed reviewer checklist, Model to use,
   First command to run. State the review status in plain words (for example:
   "Geo HOLD 1 of 2, fixes not applied").
2. Token limit, allowance GREEN or AMBER: a new session on the same AI.
   Allowance RED: a model whose meter is GREEN or AMBER. No headroom: tell the
   user the earliest reset time and stop.
3. Start the new session yourself, with the path to CONTINUE_HERE.md as its
   first message. In Claude desktop, use the session tool
   (`mcp__ccd_session_mgmt__*`). Done when the new session has replied that it
   read the file. If you cannot start one, tell the user in one line what to
   open and paste.
4. The new session checks it is in the right folder (pwd), reads
   CONTINUE_HERE.md first, and repeats this skill until Done when is met. After
   handing over, the old session starts no new work. One worker at a time per
   job; different jobs may run in parallel (see [COORDINATOR.md](COORDINATOR.md)).
5. Pay-as-you-go or extra usage only inside a cap the user has already granted.
   Never switch it on yourself.
6. On finish or handover, log the real final count:
   `PACE --tokens <tokensUsed> --worker <job> --job-type <job-type> --final`.

## 5. Report
One line: model, allowance zone and percent used, task tokens, reset time, and
where the work was handed (if anywhere). Then stop.

## 6. Parallel jobs and worker briefs
All sessions share one allowance. Before starting workers, follow
[COORDINATOR.md](COORDINATOR.md). Paste [WORKER_BLOCK.md](WORKER_BLOCK.md)
verbatim into every brief, with `<job>` filled in. Never rely on a worker
loading this skill by itself.
