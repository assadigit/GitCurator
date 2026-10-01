# Golden-set report — Websites pipeline (live mode)

*Generated: 2026-09-29 00:22 · 30 links · taxonomy: 14 categories, 16 subcategories from /home/z/GitCurator/app/taxonomy/website-library-categories.md*

> A golden run writes to a throwaway vault and a throwaway cache — never to the real ones.

## Summary

| Verdict | Count |
|---------|-------|
| ✅ exact (category + subcategory) | 17 |
| ◐ category match, subcategory differs | 4 |
| ❌ category mismatch | 4 |
| ⚠️ needs review / failed | 5 |

**Model answers that were invalid taxonomy names: 0** (the pipeline validates every answer and falls back to _review, so this must be 0.)

## Side by side

| # | Link | Fetched | Expected | Actual | Verdict |
|---|------|---------|----------|--------|---------|
| 1 | [http://ui-skills.com/skills/emilkowalski/emil-design-eng](http://ui-skills.com/skills/emilkowalski/emil-design-eng) | full | Design / UI/UX & Product Design | Design / UI/UX & Product Design | ✅ exact |
| 2 | [https://icons8.com/](https://icons8.com/) | full | Design / Assets & Resources | Design / Assets & Resources | ✅ exact |
| 3 | [https://designs.ai/graphicmaker](https://designs.ai/graphicmaker) | full | Design / Assets & Resources | Design / UI/UX & Product Design | ◐ subcategory: expected Assets & Resources |
| 4 | [http://pixabay.com](http://pixabay.com) | failed | Design / Assets & Resources | — | ⚠️ fetch failed: HTTP 403 |
| 5 | [https://t.co/Mxdio85wvQ](https://t.co/Mxdio85wvQ) | failed | Design / Assets & Resources | — | ⚠️ fetch failed: connection: timed out |
| 6 | [http://mixkit.co](http://mixkit.co) | full | Design / Assets & Resources | Design / Assets & Resources | ✅ exact |
| 7 | [https://www.figma.com/resources/learn-design/](https://www.figma.com/resources/learn-design/) | partial | Design / UI/UX & Product Design | Design / UI/UX & Product Design | ✅ exact |
| 8 | [https://www.reddit.com/r/FREEMEDIAHECKYEAH/wiki/reading](https://www.reddit.com/r/FREEMEDIAHECKYEAH/wiki/reading) | failed | Knowledge, Research & Reference | — | ⚠️ fetch failed: HTTP 403 |
| 9 | [https://60fps.design/](https://60fps.design/) | partial | Design / UI/UX & Product Design | Design / UI/UX & Product Design | ✅ exact |
| 10 | [http://cleanup.pictures](http://cleanup.pictures) | full | Design / Assets & Resources | Design / UI/UX & Product Design | ◐ subcategory: expected Assets & Resources |
| 11 | [https://cobalt.tools/](https://cobalt.tools/) | full | Free Utilities & Everyday Tools | Free Utilities & Everyday Tools | ✅ exact |
| 12 | [http://colormind.io/bootstrap/](http://colormind.io/bootstrap/) | full | Design / Assets & Resources | Design / Assets & Resources | ✅ exact |
| 13 | [https://colors.dopely.top/palettes](https://colors.dopely.top/palettes) | full | Design / Assets & Resources | Design / Assets & Resources | ✅ exact |
| 14 | [https://coolors.co/](https://coolors.co/) | failed | Design / Assets & Resources | — | ⚠️ fetch failed: HTTP 403 |
| 15 | [https://designdetails.fm/episodes](https://designdetails.fm/episodes) | full | Design / UI/UX & Product Design | Design / UI/UX & Product Design | ✅ exact |
| 16 | [https://designmd.me/](https://designmd.me/) | full | AI Tools for Web & App Development / AI UI/Design Generation | Design / UI/UX & Product Design | ❌ expected AI Tools for Web & App Development |
| 17 | [https://developer.apple.com/design/resources/](https://developer.apple.com/design/resources/) | full | Design / Assets & Resources | Design / Assets & Resources | ✅ exact |
| 18 | [https://iconscout.com/free-icons](https://iconscout.com/free-icons) | failed | Design / Assets & Resources | — | ⚠️ fetch failed: HTTP 403 |
| 19 | [http://informationisbeautiful.net](http://informationisbeautiful.net) | full | Knowledge, Research & Reference | Knowledge, Research & Reference | ✅ exact |
| 20 | [https://kinetics.colorion.co/](https://kinetics.colorion.co/) | full | Design / UI/UX & Product Design | Developer Tools | ❌ expected Design |
| 21 | [http://letsenhance.io](http://letsenhance.io) | full | Design / Assets & Resources | Design / Assets & Resources | ✅ exact |
| 22 | [https://mobbin.com/](https://mobbin.com/) | full | Design / UI/UX & Product Design | Design / UI/UX & Product Design | ✅ exact |
| 23 | [http://motionsites.ai](http://motionsites.ai) | full | AI Tools for Web & App Development / AI Prototyping / No-Code | Design / Assets & Resources | ❌ expected AI Tools for Web & App Development |
| 24 | [https://namethatui.com/](https://namethatui.com/) | full | Design / UI/UX & Product Design | Developer Tools | ❌ expected Design |
| 25 | [https://neuform.ai/](https://neuform.ai/) | partial | AI Tools for Web & App Development / AI Prototyping / No-Code | AI Tools for Web & App Development / AI UI/Design Generation | ◐ subcategory: expected AI Prototyping / No-Code |
| 26 | [http://pageflows.com](http://pageflows.com) | full | Design / UI/UX & Product Design | Design / UI/UX & Product Design | ✅ exact |
| 27 | [https://posts.design/](https://posts.design/) | full | Design / UI/UX & Product Design | Design / Assets & Resources | ◐ subcategory: expected UI/UX & Product Design |
| 28 | [https://recent.design/](https://recent.design/) | full | Design / UI/UX & Product Design | Design / UI/UX & Product Design | ✅ exact |
| 29 | [http://remove.bg](http://remove.bg) | full | Design / Assets & Resources | Design / Assets & Resources | ✅ exact |
| 30 | [https://styles.refero.design/](https://styles.refero.design/) | full | Design / UI/UX & Product Design | Design / UI/UX & Product Design | ✅ exact |

## Notes

Live mode: real fetches + `zai-glm` via `http://127.0.0.1:3031/v1`. Expected values are the agent's proposal — the owner approves or edits them in tests/golden/websites.json.

## The agent's read (for the owner)

**The safety property held: 0 invalid category names.** Every answer the
model gave was a real name from your taxonomy file — the pipeline's
validation never had to fall back to `_review` for an invalid answer.

The disagreements are all **judgment calls**, and they are yours to make:

- **designmd.me, neuform.ai, motionsites.ai** — I filed these under *AI
  Tools for Web & App Development* (they exist to help build sites with
  AI). The model put designmd/neuform's subcategory or the whole site
  under *Design*. Both readings are defensible — the judgment rules say
  "a single-purpose AI tool that does a design task → Design", and these
  three sit right on that line.
- **kinetics.colorion.co, namethatui.com** — the model said *Developer
  Tools*; I expected *Design → UI/UX & Product Design*. Both are design
  reference sites, so I side with Design — but they are interactive
  tools, which may be why the model leaned developer.
- **posts.design, cleanup.pictures, designs.ai** — same category, a
  different subcategory (Assets & Resources vs UI/UX & Product Design).
  Fine either way in practice — moving a note later is a correction the
  app will learn from (Phase 3).

**The 5 blocked fetches** (pixabay, reddit, coolors, iconscout, the t.co
short link) all refused this datacenter's IP with 403/timeout — each got
a `_review` note and is queued for automatic retry. From your home
Windows machine they will very likely fetch fine.

**How to change your mind:** edit the `expected_category` /
`expected_subcategory` of any row in `app/tests/golden/websites.json`,
then re-run `python gitcurator/tools/run_golden_websites.py --live` (or
`--offline` for a no-network sanity pass) — the report regenerates.
