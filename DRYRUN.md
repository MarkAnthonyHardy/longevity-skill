# Dry run

Required before work starts unless the job is expected under about 50K tokens.
The coordinator runs or requires one for each such job before starting it.

Read-only planning pass (plan mode, or simply no edits) on sonnet or haiku. Write
`~/.claude/handovers/<job>/DRYRUN.md` with these headings, all filled in:
- Steps
- Files and tools
- Tests that prove it worked
- Token estimate: `PACE --history <job-type>` when history exists, otherwise a rough estimate
- Split plan: required if the high estimate is 160K or more, else "none"
- Risks or questions

Real work starts from that file.
