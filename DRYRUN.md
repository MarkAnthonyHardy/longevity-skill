# Dry run

Required before work starts, unless the job is expected under about 50K tokens
(a short, single-file edit). The coordinator runs or requires this for each
job before starting it.

Do a read-only planning pass (Claude Code plan mode, or simply make no edits)
on sonnet or haiku. Write `~/.claude/handovers/<job>/DRYRUN.md` with exactly
these headings, all filled in:

- Steps
- Files and tools
- Tests that prove it worked
- Token estimate — `python3 ~/.claude/tools/pace.py --history <job-type>` when
  history exists, otherwise a rough estimate
- Split plan — required if the high estimate is 160K tokens or more; otherwise
  "none"
- Risks or questions

Real work starts from that file. Do not edit the repo during the dry run.
