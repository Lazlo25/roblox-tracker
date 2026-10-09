# Deep Analysis guide

The goal is long-running: each day, test earlier ideas against fresh data, add new ones, and move confidence up only
when a pattern keeps holding across more games and more days. Aim past what is trending this month toward what has
always driven these charts and always will. Prefer new, non-obvious, increasingly well-supported, practically useful
patterns over more descriptive statistics.

## The four pillars

- **Most Popular: Sustained Dominance.** Higher positions, longer presence (total and consecutive days) and higher peak positions.
- **Top Earning: Monetization Efficiency.** A high earning position relative to CCU. The Monetization Score ranks position × CCU (lower is better).
- **Top Trending: Early Momentum.** Rapid upward movement, for games 0–4 months old only. Ignore older games for this pillar.
- **Up-and-Coming: Breakout Popularity.** Higher positions within the chart.

For Top Trending and Up-and-Coming, compare games of similar age (the report gives within-age effects). When two games'
trajectories differ, consider whether the age gap alone explains it.

## What the page shows

The page shows only each chart's title keywords and an **Actionable steps** table: every active hypothesis that carries
an `action`, with its confidence bar and status. Everything else (observations, patterns, outliers, statements,
evidence) stays in the backend and must stay just as thorough, because it is what the steps and their confidence rest on.

- Give a hypothesis an `action` only when it points to a choice a developer can make (pricing, loop, title, thumbnail,
  update timing). Observations, lifecycle facts and measurement caveats get none.
- Word actions as short imperatives, ideally 3–7 words, with no hedging (the confidence bar carries the doubt): "Price
  most dev products above 79 R$", "Avoid all-caps titles", "Ship the big update before week 6". Use R$ for Robux.
- Keep each action true to its hypothesis: it may not claim more than the association supports. When the data turns
  against an idea, keep the action and let its confidence fall; don't rewrite it to match.

## Daily steps (after `analyze.py stats`)

1. Read `work/<DATE>/stats_report.md`, and yesterday's `analysis/archive/<previous date>.json` for continuity.
2. Write `work/<DATE>/proposals.json`:

```json
{
  "new": [
    {"chart": "te", "statement": "Games selling server-wide boosts (nukes, global luck) earn a higher position for their CCU",
     "action": "Sell server-wide boosts",
     "test": {"feature": "prod_server_wide", "metric": "te_eff", "type": "binary", "direction": "+"}},
    {"chart": "tt", "statement": "Momentum fades once a game passes about 30 days old",
     "test": {"feature": "age_days", "metric": "tt_mom", "type": "threshold", "cut": 30, "direction": "-"}},
    {"chart": "uc", "statement": "Higher like ratios go with higher Up-and-Coming positions within the same age band",
     "test": {"feature": "like_ratio", "metric": "uc_pos", "type": "corr", "direction": "+", "strata": "age"}},
    {"chart": "x", "kind": "qualitative", "confidence": 25,
     "statement": "Games reach Up-and-Coming before Top Trending",
     "rationale": "why you think so, and what data would confirm it"}
  ],
  "qualitative_updates": [{"id": "H-x-001", "confidence": 30, "note": "what changed"}],
  "actions": [{"id": "H-uc-004", "action": "Use a plain, descriptive title"}, {"id": "H-mp-008", "action": ""}],
  "retire": [{"id": "H-mp-004", "reason": "why"}]
}
```

   - `chart` is mp, te, tt, uc, or x for cross-chart.
   - Testable hypotheses use a feature and metric from the report. Types: `binary` (has the trait vs not), `corr`
     (rank correlation), `threshold` (above vs below `cut`). `direction` "+" means the trait, or a higher value, goes
     with a better pillar result.
   - Metrics: mp_pos, mp_days, mp_peak, te_eff, te_pos, tt_mom, tt_pos, uc_pos.
   - Feature names appear in the report's association lines. They include page data (passes_n, products_median_price,
     like_ratio, fav_per_kvisit, days_since_update, updates_per_week, max_players, badges_n, has_video,
     creator_chart_games, group_members_log), title and description signals (title_*, mech_*, cta_*, pass_*, prod_*,
     genre=...), and gameplay-profile signals (loop=..., thumb=..., complexity, novelty, social=..., competitive_core).
   - The script tests tested hypotheses itself and sets their confidence. Never set those by hand.
   - Use `qualitative` for ideas the features can't test yet (thumbnail stories, sequences, causes). Keep their
     confidence honest and low until evidence builds.
   - Avoid duplicates of existing hypotheses (the report lists them).
   - `action` (optional, see "What the page shows") is the step the page displays. `actions` sets, rewords or (with
     an empty string) removes the action of existing hypotheses; the report's hypothesis log shows each one's current
     action. Every actionable insight should exist as a hypothesis with an action: tested when a feature can test it,
     qualitative otherwise.
3. Write `work/<DATE>/narrative.json`:

```json
{
  "charts": {
    "mp": {
      "observations": ["3–6 sentences: notable patterns by position, movement and age today"],
      "patterns": ["keyword, thumbnail, video and description patterns, what they suggest about gameplay, and how they relate to the pillar"],
      "outliers": [{"name": "Game name", "note": "how it breaks the pattern and a likely reason"}]
    },
    "te": {}, "tt": {}, "uc": {}
  },
  "cross": {
    "summary": ["patterns that span several charts"],
    "progressions": ["observed chart-to-chart sequences, with counts"]
  }
}
```

   Fill in all four charts. Each list should run 2–6 items, in plain sentences.
   - **Confidence wording:** at 70 and above say "consistently", at 50–69 "tends to", at 30–49 "may", and below 30
     "speculative".
   - **Causation:** phrase findings as associations unless you have real evidence of cause.
   - **Confounders:** name them when they plausibly explain a pattern: one creator with several games, game age, genre
     mix, or a seasonal event.
4. Run `python3 analyze.py finalize --date <DATE>`, then write `work/<DATE>/analysis.json` to the artifact
   (collection `analysis`, doc id `<DATE>`).
