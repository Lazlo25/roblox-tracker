# 4-hourly cloud run (tracker update + daily Deep Analysis)

Fires at about 11 PM, 3 AM, 7 AM, 11 AM, 3 PM and 7 PM Eastern (an hour earlier from November to March, because the
schedule is set in UTC). Work in the `roblox-tracker` checkout; the `roblox-ccu` checkout (the hourly GitHub Actions
data: CCU, visits, votes, RoTerminal's total players) sits next to it. The routine's prompt sets MODE (`live` or `dryrun`).

| MODE | git branch | collections written |
|---|---|---|
| live | main | `days`, `analysis`, `live` |
| dryrun | dryrun | `dryrun_days`, `dryrun_analysis`, `dryrun_live` |

Artifact URL: https://claude.ai/artifact/3GEguDQy6AVzUpckAVomYs

Ground rules: don't contact Roblox or any website (this sandbox can't; everything needed is in the two checkouts).
Don't edit the scripts, guides, workflows or the artifact page. Write only the database documents named below and the
files the steps name. If a step fails in a way these steps don't cover, stop and report the error text.

ArtifactData writes (load the tool with ToolSearch "select:ArtifactData" if needed): call action "get" first; if the
document exists, "set" with its version as if_version, otherwise "set" without one. DAYS/ANALYSIS/LIVE are the MODE's
collections from the table.

## Steps

1. `cd` into roblox-tracker, check out the MODE's branch and `git pull --rebase`; then
   `git -C ../roblox-ccu checkout -q main && git -C ../roblox-ccu pull -q --rebase` (the checkouts start detached).
   Set D = `TZ=America/New_York date +%F`, PREV = the day before D, and H = `TZ=America/New_York date +%H` (00–23).
2. Total players: run `python3 tracker.py live --ccu ../roblox-ccu --out /tmp/live.json` and write LIVE doc `platform`
   from file `/tmp/live.json`. If it prints a line starting with "NO LIVE DATA", note it and carry on.
3. Today's snapshot (GitHub Actions records it at 1 PM Eastern):
   - If `db-copy/days/D.json` doesn't exist and 8 ≤ H < 13: write the current UTC time (`date -u +%FT%TZ`) to
     `trigger/daily.txt`, commit "Start daily snapshot D" and push. The workflow waits for 1 PM by itself. Go to step 6.
   - If `db-copy/days/D.json` doesn't exist and H ≥ 13: push the trigger the same way (the workflow starts at once), then
     `git pull --rebase` every 2 minutes for up to 30 minutes until `db-copy/days/D.json` appears. If it never does,
     remember "Snapshot for D didn't arrive" for the reply and go to step 6.
   - If H < 8, go to step 6.
4. Today's figures: run `python3 tracker.py refresh --date D --dump db-copy --day-file ../roblox-ccu/data/daily/D.json
   --rt-file ../roblox-ccu/data/roterminal_daily.json --out /tmp/day.json`. If it prints a line starting with
   "NO UPDATE", skip to step 5; otherwise `mv /tmp/day.json db-copy/days/D.json` and mark D as changed.
5. Deep Analysis, once a day: only if H ≥ 14 and `analysis/archive/D.json` doesn't exist.
   a. Contact sheets: `mkdir -p work/D && git fetch -q origin sheets && git archive origin/sheets | tar -x -C work/D`.
   b. Gameplay profiling. Read `PROFILE_GUIDE.md` once. For each `work/D/dossier_NN.md`: read it, look at its contact
      sheets (dossier_01 covers sheet_01–03, dossier_02 sheet_04–06, and so on; view them with the Read tool) and
      write `work/D/labels_NN.json` exactly as the guide describes. At most 4 dossiers; skip if there are none.
   b2. Gameplay videos (long-running hits). `mkdir -p work/videos && git fetch -q origin videos && git archive origin/videos | tar -x -C work/videos`
      (skip if the branch doesn't exist). For each folder in `work/videos/` whose game is `frames_ready` in
      `videos/state.json` (at most 6): read `meta.json`, look at its `sheet_*.jpg` with the Read tool and write
      `video_labels.json` in that folder exactly as `VIDEO_GUIDE.md` describes (read it once).
   c. Run `python3 video.py ingest`, `python3 analyze.py ingest --date D`, then `python3 analyze.py stats --date D`.
   d. Synthesis. Read `ANALYSIS_GUIDE.md`, then `work/D/stats_report.md`, then the most recent earlier file in
      `analysis/archive/`. Write `work/D/proposals.json` and `work/D/narrative.json` exactly as the guide describes,
      building on yesterday's analysis.
   e. Run `python3 analyze.py finalize --date D`. If it says the document is too large, shorten narrative.json and rerun.
   f. Write ANALYSIS doc D from file `work/D/analysis.json`.
6. Yesterday: if `db-copy/days/PREV.json` exists and its `avg24.through_et` isn't "24:00", refresh it with its
   full-day figures: the step 4 command with PREV in place of D (both `--date` and the day file). If it updates,
   `mv /tmp/day.json db-copy/days/PREV.json` and mark PREV as changed.
7. Write DAYS doc D and/or PREV from `db-copy/days/<date>.json` for each date marked as changed (and D whenever step 5
   ran).
8. `git add -A db-copy analysis profiles work trigger videos && git commit -m "Cloud run D H:00"`, then `git push` (on
   rejection: `git pull --rebase` and push again, up to 3 times). Skip if there's nothing to commit.
9. Reply in up to three short lines: "Players: <now from step 2>", "Day D: <snapshot status, and the OK/EXTRAS lines
   from step 4>", and, if step 5 ran, "Analysis: <hypotheses count> hypotheses, <N> games profiled today, <one-sentence
   headline finding>".
