# Gameplay profiling guide

Each day `analyze.py collect` queues up to 60 games that have no gameplay profile yet (or whose description changed).
For each queued game you have:

- `work/<DATE>/dossier_NN.md`: 15 games per file. Title, charts and positions, age, genre/subgenre, creator,
  visits, favorites, like ratio, game passes with prices, developer products with prices, badges, page media,
  and the description.
- `work/<DATE>/sheet_NN.jpg`: 5 games per image. Each row is labelled `Qxx · name` and shows the game icon,
  then up to three page thumbnails.

Read a dossier, look at the matching sheets (Q01–Q05 are on sheet_01, Q06–Q10 on sheet_02, and so on), then write
`work/<DATE>/labels_NN.json` with one object per game, using the same NN as the dossier. Work from what the page
shows; never open or play the game. Guessing is allowed: put your certainty in `confidence` (0–100) and say "unclear"
rather than inventing detail.

```json
[
  {
    "label": "Q01",
    "loop": "steal eggs from giant creatures -> place them on your base to earn $/s -> buy upgrades -> reach rarer areas",
    "loop_archetype": "steal_defend_base",
    "objectives": "collect the rarest eggs and grow income per second",
    "progression": "income per second, rarer egg tiers, rebirths",
    "instructions": "what the description tells players to do, briefly",
    "mechanics": ["stealing", "base defense", "income per second", "rebirth"],
    "social": "pvp",
    "competitive": "light",
    "unique": ["anything unusual, or an unusual combination of familiar mechanics"],
    "themes": ["dinosaurs", "eggs"],
    "audience": "kids",
    "trend_hooks": ["steal-a-x"],
    "monetization_signals": ["x2 money pass", "money bundles", "server-wide luck"],
    "update_signals": ["weekly updates", "Halloween event"],
    "thumbnails": "what the icon and thumbnails advertise: gameplay shown, rewards, big numbers, characters, events",
    "thumbnail_style": "reward_flex",
    "thumb_text_numbers": true,
    "video": "none",
    "complexity": 2,
    "complexity_notes": "single map, simple UI, NPC creature AI, egg models; no complex animation",
    "novelty": 1,
    "confidence": 75
  }
]
```

Allowed values:

- `loop_archetype`: collect_upgrade_rebirth, idle_income_tycoon, steal_defend_base, incremental_plus1, hatch_collect_pets,
  rng_roll_collect, merge_upgrade, obby_platformer, tower_defense, combat_pvp, wave_survival_coop, horror_escape,
  roleplay_social, sports_competitive, racing_driving, simulation_job, sandbox_physics, party_minigames, story_adventure, other
- `social`: solo, coop, pvp, social_hub, mixed
- `competitive`: none, light, core
- `audience`: young_kids, kids, teens, broad, older
- `thumbnail_style`: gameplay, character_art, reward_flex (big numbers, rare items), text_ad, event_banner, mixed
- `complexity`: 1 (a hobbyist could build it in days) to 5 (a studio-scale build: large maps, custom animation, complex systems)
- `novelty`: 0 (a clone of a common template) to 3 (a genuinely new idea)

Every key above is required; use "unclear", [] or "none" when the page doesn't say.
Then run `python3 analyze.py ingest --date <DATE>`.
