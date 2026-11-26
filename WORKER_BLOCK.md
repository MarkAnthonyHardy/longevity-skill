# Worker block

Paste this verbatim into every worker brief. Replace `<job>` and `<job-type>`.

```
USAGE RULES (Longevity): load the longevity skill first. Unless this job is
small (under ~50K), start from ~/.claude/handovers/<job>/DRYRUN.md; if it does
not exist, write it in a read-only pass on sonnet or haiku before any edits.
Read the real token count from get_usage (`context.tokensUsed`); never guess.
Then run `python3 ~/.claude/tools/pace.py --tokens <tokensUsed> --worker <job>`.
Do that at the start, after each finished step, and before any large file read,
test run, subagent, or step expected to add more than about 20K tokens. If you
only have a guess, add `--estimated` (thresholds 130K / 170K). At 160K (130K if
estimated): finish the current step, start no new one, and write handover notes.
At 200K (170K if estimated): stop after the current tool call and hand over now.
On finish or handover, log the real final count:
`python3 ~/.claude/tools/pace.py --tokens <tokensUsed> --worker <job> --job-type <job-type> --final`.
Handover notes go in ~/.claude/handovers/<job>/CONTINUE_HERE.md (outside the
repo, so a "no repo edits" rule does not apply) and must state the review
status in plain words (for example: "Geo HOLD 1 of 2, fixes not applied").
Then start the new session yourself with the path to that file as its first
message; do not ask the user. If another session is already working on this
job, stand down. The allowance is shared with the other jobs: do not start
parallel agents or repeat full test runs unless the coordinator says so.
```
