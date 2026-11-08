# Daily cloud run (Deep Analysis)

Fires at about 3 PM and 7 PM Eastern (4 PM and 8 PM in summer, because the schedule is set in UTC). The 7 PM run is a
retry: it does nothing when the 3 PM run already finished the analysis. Work in the `roblox-tracker` checkout; the
`roblox-ccu` checkout (the hourly GitHub Actions data) sits next to it.

Everything else runs without Claude: the hourly Publish site job rebuilds the hosted page
(https://lazlo25.github.io/roblox-tracker/) and starts the Daily snapshot, which records the day at 1 PM Eastern and
finalises yesterday. The claude.ai artifact is no longer updated, so don't write to ArtifactData.

Ground rules: don't contact Roblox or any website (this sandbox can't; everything needed is in the two checkouts).
Don't edit the scripts, guides or workflows. Write only the files the steps name. If a step fails in a way these steps
don't cover, stop and report the error text.

## Steps

1. `cd` into roblox-tracker, `git checkout -q main && git pull -q --rebase`; then
   `git -C ../roblox-ccu checkout -q main && git -C ../roblox-ccu pull -q --rebase` (the checkouts start detached).
   Set D = `TZ=America/New_York date +%F` and H = `TZ=America/New_York date +%H` (00–23).
2. Stop and reply "Analysis for D already done" if `analysis/archive/D.json` exists. Stop and reply "No snapshot for D
   yet" if H < 14 or `db-copy/days/D.json` doesn't exist.
3. Today's figures: run `python3 tracker.py refresh --date D --dump db-copy --day-file ../roblox-ccu/data/daily/D.json
   --rt-file ../roblox-ccu/data/roterminal_daily.json --out /tmp/day.json`. Unless it prints a line starting with
   "NO UPDATE", `mv /tmp/day.json db-copy/days/D.json`.
4. Deep Analysis:
   a. Contact sheets: `mkdir -p work/D && git fetch -q origin sheets && git archive origin/sheets | tar -x -C work/D`.
   b. Gameplay profiling. Read `PROFILE_GUIDE.md` once. For each `work/D/dossier_NN.md`: read it, look at its contact
      sheets (dossier_01 covers sheet_01–03, dossier_02 sheet_04–06, and so on; view them with the Read tool) and
      write `work/D/labels_NN.json` exactly as the guide describes. At most 4 dossiers; skip if there are none.
   c. Gameplay videos (long-running hits). `mkdir -p work/videos && git fetch -q origin videos && git archive origin/videos | tar -x -C work/videos`
      (skip if the branch doesn't exist). For each folder in `work/videos/` whose game is `frames_ready` in
      `videos/state.json` (at most 6): read `meta.json`, look at its `sheet_*.jpg` with the Read tool and write
      `video_labels.json` in that folder exactly as `VIDEO_GUIDE.md` describes (read it once).
   d. Run `python3 video.py ingest`, `python3 analyze.py ingest --date D`, then `python3 analyze.py stats --date D`.
   e. Synthesis. Read `ANALYSIS_GUIDE.md`, then `work/D/stats_report.md`, then the most recent earlier file in
      `analysis/archive/`, then any file in `analysis/research/` not listed in `analysis/research/ingested.txt` (see the
      guide's "Outside research"). Write `work/D/proposals.json` and `work/D/narrative.json` exactly as the guide describes,
      building on yesterday's analysis.
   f. Run `python3 analyze.py finalize --date D`. If it says the document is too large, shorten narrative.json and rerun.
5. `git add -A db-copy analysis profiles work videos && git commit -m "Cloud analysis D"`, then `git push` (on
   rejection: `git pull --rebase` and push again, up to 3 times). The push makes Publish site rebuild the page.
6. Reply in up to two short lines: "Day D: <the OK/EXTRAS lines from step 3>" and "Analysis: <hypotheses count>
   hypotheses, <N> games profiled today, <one-sentence headline finding>".
