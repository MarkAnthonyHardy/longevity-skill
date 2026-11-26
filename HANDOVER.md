# Handover

Automatic, with no waiting for the user.

1. Write `~/.claude/handovers/<job>/CONTINUE_HERE.md` (outside the repo, so a
   "no repo edits" brief never blocks it). Headings: Goal, Done when, Branch/PR
   links, Agreed reviewer checklist, Model to use, First command to run. State
   the review status in plain words (for example "Geo HOLD 1 of 2, fixes not applied").
2. Choose where it opens: token limit with allowance GREEN or AMBER, a new
   session on the same AI; allowance RED, a model whose meter is GREEN or AMBER;
   no headroom, tell the user the earliest reset time and stop.
3. Start the new session yourself with the path to CONTINUE_HERE.md as its first
   message (Claude desktop: `mcp__ccd_session_mgmt__*`). Done when it has replied
   that it read the file. If you cannot start one, tell the user in one line what to open and paste.
4. The new session checks `pwd`, reads CONTINUE_HERE.md first, and repeats the
   skill until Done when is met. The old session starts no new work.
5. One worker at a time per job. Different jobs may run in parallel.
6. Log the real final count (see SKILL.md step 6).
