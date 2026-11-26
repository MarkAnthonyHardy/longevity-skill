# Coordinator

All sessions share one allowance. Size jobs together before starting any.

1. Run or require a dry run for each job that is not small ([DRYRUN.md](DRYRUN.md)).
2. Size each with `PACE --history <job-type>`. If the expected size or
   historical high is 160K or more, split it first. Then check the shared allowance:
   `PACE --model <m> --usage-json <file> --estimate <pct> --sessions <N>`
   (`--estimate` is one job, N the parallel jobs). It prints each worker's SHARE.
   DOES NOT FIT: run fewer at once or use a cheaper model for some.
3. Paste [WORKER_BLOCK.md](WORKER_BLOCK.md) verbatim into every brief. Never rely
   on a worker loading the skill itself.
4. Never tell a worker "no CONTINUE_HERE" without adding that handover notes go
   to `~/.claude/handovers/<job>/`.
5. Re-read the meter and re-run PACE each time a job reports. Start no new job in
   RED. Any worker at 160K (130K if estimated) gets a handover now.
6. Never report a job's state from memory. Re-read the worker's final message,
   the PR, or the reviewer's latest reply first.
7. When a worker hands over, make sure a successor session is started (start it
   yourself if needed) and log it. On each check, scan
   `~/.claude/handovers/*/CONTINUE_HERE.md` for any without a live successor.
