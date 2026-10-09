# Evening CCU refresh (cloud)

Updates today's CCU with the latest day-so-far averages. Work in the `roblox-tracker` checkout; `roblox-ccu` is the
checkout next to it. No analysis. Artifact URL: https://claude.ai/artifact/3GEguDQy6AVzUpckAVomYs

Ground rules: don't contact any website. Write only today's `days` document. If a step fails in a way these steps
don't cover, stop and report the error text.

1. `cd` into roblox-tracker, `git checkout main && git pull --rebase`. Set D = `TZ=America/New_York date +%F`.
   If `db-copy/days/D.json` doesn't exist, stop and reply "No snapshot for D yet; nothing to refresh."
2. Run `python3 tracker.py refresh --date D --dump db-copy --day-file ../roblox-ccu/data/daily/D.json --out /tmp/day.json`.
   If it prints a line starting with "NO UPDATE", stop and reply with that line. Otherwise `mv /tmp/day.json db-copy/days/D.json`.
3. ArtifactData (load with ToolSearch "select:ArtifactData" if needed): action "get", collection "days", doc D; then
   "set" with that version as if_version and file_path `db-copy/days/D.json`.
4. `git add db-copy && git commit -m "Evening CCU D"`, then `git push` (on rejection: `git pull --rebase`, push again,
   up to 3 times).
5. Reply with the OK line and the CCU SOURCE line from step 2.
