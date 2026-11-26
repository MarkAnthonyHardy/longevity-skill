# Coordinator

All sessions share one allowance. Size jobs together before starting any.

1. Run or require a dry run for each job that is not small (see
   [DRYRUN.md](DRYRUN.md)). Start the real work from that file.
2. Size each job with `python3 ~/.claude/tools/pace.py --history <job-type>`
   (falls back to a rough estimate when there is no history). If the expected
   size or historical high is 160K tokens or more, split it before starting.
   Then check the shared allowance:
   `python3 ~/.claude/tools/pace.py --model <m> --usage-json <file> --estimate <pct> --sessions <N>`
   (`--estimate` is one job's size, N the number of parallel jobs). It checks
   the total and prints each worker's SHARE. DOES NOT FIT: run fewer at once,
   or use a cheaper model for some.
3. Paste [WORKER_BLOCK.md](WORKER_BLOCK.md) verbatim into every brief, with
   `<job>` filled in. Never rely on a worker loading the skill by itself.
4. Never tell a worker "no CONTINUE_HERE" without adding that handover notes
   go to `~/.claude/handovers/<job>/` (outside the repo, so allowed).
5. Re-read the meter and re-run PACE each time a job reports. Start no new job
   in RED, and tell the others to finish their step if the zone worsens. Any
   worker at 160K tokens or more (130K if its count is estimated) gets a
   handover now.
6. Never report a job's state from memory. Re-read the worker's final message,
   the PR, or the reviewer's latest reply before reporting.
7. Whenever a worker hands over, make sure a successor session is started
   (start it yourself if needed) and record that you did. No orphaned handover
   notes. On each check, scan `~/.claude/handovers/*/CONTINUE_HERE.md` for any
   without a live successor.
