# Daily cloud run (Deep Analysis + tracker update)

Runs at about 1:45 PM Eastern (retry 2:45), after the 1 PM GitHub Actions snapshot; the 2 PM local task adds the 18+ titles afterwards. Work in the `roblox-tracker` checkout; the
`roblox-ccu` checkout sits next to it. The routine's prompt sets MODE (`live` or `dryrun`).

| MODE | git branch | collections written |
|---|---|---|
| live | main | `days`, `analysis` |
| dryrun | dryrun | `dryrun_days`, `dryrun_analysis` |

Artifact URL: https://claude.ai/artifact/3GEguDQy6AVzUpckAVomYs

Ground rules: don't contact Roblox or any website (this sandbox can't, and the snapshot is already in the repo).
Don't edit the scripts, guides, workflows or the artifact page. Write only the database documents named below. If a
step fails in a way these steps don't cover, stop and report the error text.

## Steps

1. `cd` into roblox-tracker, check out the MODE's branch and `git pull --rebase`. Set D = `TZ=America/New_York date +%F`
   and PREV = the day before D. If `db-copy/days/D.json` doesn't exist, stop and reply
   "No 1 PM snapshot for D yet (GitHub Actions hasn't run)." If `analysis/archive/D.json` already exists, stop and
   reply "Already done for D." (this routine fires twice a day in case the snapshot was late).
2. Contact sheets: `mkdir -p work/D && git fetch -q origin sheets && git archive origin/sheets | tar -x -C work/D`.
3. Gameplay profiling. Read `PROFILE_GUIDE.md` once. For each `work/D/dossier_NN.md`: read it, look at its contact
   sheets (dossier_01 covers sheet_01–03, dossier_02 sheet_04–06, and so on; view them with the Read tool) and
   write `work/D/labels_NN.json` exactly as the guide describes. At most 4 dossiers; skip if there are none.
4. Run `python3 analyze.py ingest --date D`, then `python3 analyze.py stats --date D`.
5. Synthesis. Read `ANALYSIS_GUIDE.md`, then `work/D/stats_report.md`, then the most recent earlier file in
   `analysis/archive/`. Write `work/D/proposals.json` and `work/D/narrative.json` exactly as the guide describes,
   building on yesterday's analysis.
6. Run `python3 analyze.py finalize --date D`. If it says the document is too large, shorten narrative.json and rerun.
7. Write the database (ArtifactData; load it with ToolSearch "select:ArtifactData" if needed). For each of:
   - DAYS collection, doc D, file `db-copy/days/D.json`
   - DAYS collection, doc PREV, file `db-copy/days/PREV.json` (yesterday, finalized with its full-day CCU average)
   - ANALYSIS collection, doc D, file `work/D/analysis.json`

   call action "get" first; if the document exists, "set" with its version as if_version, otherwise "set" without
   one. DAYS/ANALYSIS are `days`/`analysis` in live mode and `dryrun_days`/`dryrun_analysis` in dryrun mode.
8. `git add -A && git commit -m "Analysis D"`, then `git push` (on rejection: `git pull --rebase` and push again,
   up to 3 times).
9. Reply with two short lines: "Snapshot D: <games on each chart from db-copy/days/D.json, and whether it was
   patched with 18+ titles (field patched18)>" and "Analysis: <hypotheses count> hypotheses, <N> games profiled
   today, <one-sentence headline finding>".
