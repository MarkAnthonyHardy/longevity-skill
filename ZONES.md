# Model choice and allowance zones

## Pick the model
Use the model the user names. Otherwise: opus for hard reasoning, design and
reviews; sonnet for normal coding; haiku for mechanical steps; fable only when
asked. Relative costs: `~/.claude/tools/model-costs.json`.

Check it fits (`--estimate` is a percent of the allowance on sonnet):
`PACE --model <opus|sonnet|haiku|fable> --usage-json <get_usage file> --estimate <pct>`
- FITS: go ahead. TIGHT: work in AMBER, using the SUGGEST model for routine parts.
- DOES NOT FIT: use the cheaper SUGGEST model, or split the job.

## Allowance zones
`PACE --model <name> --usage-json <file>` (or `--used` / `--resets-at` /
`--window-hours` 168 weekly, 5 for a 5-hour window). The headline is the tighter
of the all-models window and the model's own window.
- GREEN: projected under 85% by reset. Work normally.
- AMBER: projected 85–99%, or under 25% left. Cheaper model for mechanical steps,
  no parallel agents, no repeat test suites, short replies, read only the file
  ranges you need.
- RED: under 10% left, or projected to hit 100% before reset. Finish the current
  step only, then hand over.

## Where the meter comes from
- Claude desktop: `mcp__ccd_session_mgmt__get_usage` (`plan.windows[]` has
  `percentUsed` and `resetsAt`; `context.tokensUsed` has the task's tokens). Save the JSON for PACE.
- Codex: `PACE --codex` and `PACE --codex-context`. STALE: do one turn, then read again.
- Any other AI: ask the user once for percent used and reset time; count tokens
  as about 4 characters per token (use `--estimated`). Without an allowance answer, work in AMBER.
