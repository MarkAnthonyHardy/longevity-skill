# PACE flags

`python3 ~/.claude/tools/pace.py` — called PACE in the skill.

- `--tokens <n>` — this task's tokens. Use `context.tokensUsed` from
  `mcp__ccd_session_mgmt__get_usage` (or `PACE --codex-context`). Never guess.
  Zones: OK under 160K; FINISH STEP, THEN HAND OVER at 160K–199K; HANDOVER NOW
  at 200K or more.
- `--estimated` — with `--tokens` only, when the count is a guess. Thresholds
  become 130K / 170K.
- `--worker <job>` — with `--tokens`, also writes one log line for that worker.
- `--job-type <label> --final` — with `--tokens`, on finish or handover, logs
  the real final tokens for that type of job (estimated finals are ignored by
  `--history`).
- `--history <job-type>` — median and high final tokens for past jobs of that
  type, or `NO HISTORY` (use a rough estimate). Prints `SPLIT` if the high is
  160K or more.
- `--model <name> --usage-json <file>` — allowance zone from a saved get_usage
  JSON (or `--used` / `--resets-at` / `--window-hours`).
- `--estimate <pct> --sessions <N>` — job size as a percent of the default
  model (sonnet). With `--sessions`, checks the total of N workers who share
  one allowance and prints each worker's SHARE (remaining minus the 5% reserve).

`--help` and `--self-test` are also available. Relative costs live in
`~/.claude/tools/model-costs.json`.
