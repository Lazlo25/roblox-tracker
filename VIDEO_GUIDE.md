# Gameplay video labeling guide

Long-running hits (see `video.py`) get one gameplay video analysed. For each folder `work/videos/<universe id>/` there is
`meta.json` (game, source `roblox` or `youtube`, video title) and one or two contact sheets `sheet_1.jpg`, `sheet_2.jpg`
(12 frames each, left to right and top to bottom, time stamped; a Roblox preview video covers the whole clip, a YouTube
video its first 20 minutes after the intro). Look at every sheet with the Read tool, then write
`work/videos/<universe id>/video_labels.json`: one JSON object with every key below. Describe only what the frames show;
use "unclear", [] or "none" when they don't. Run `python3 video.py ingest` afterwards.

```json
{
  "loop_observed": "what the player actually does, in order, in one sentence",
  "pacing": "slow | medium | fast",
  "hud_density": 3,
  "visual_polish": 4,
  "camera": "first_person | third_person | top_down | side_2d | mixed",
  "monetization_visible": ["shop button on the HUD", "x2 cash pass icon", "limited-time offer banner"],
  "retention_hooks": ["daily reward timer", "event countdown", "quest tracker", "leaderboard"],
  "social_visible": "none | light | core",
  "session_depth": "what the first minutes show compared with later minutes (new systems, areas, goals)",
  "notable": ["anything a developer copying this game should know"],
  "matches_page": true,
  "confidence": 70
}
```

- `hud_density`: 1 (almost no on-screen UI) to 5 (crowded with buttons, timers, counters).
- `visual_polish`: 1 (default Roblox look) to 5 (custom lighting, effects, animation, art direction at studio level).
- `matches_page`: false when the video shows a very different game from the page's description (an older version, a different mode).
- `confidence`: 0–100, lower when the video is short, dark, or mostly a menu.
