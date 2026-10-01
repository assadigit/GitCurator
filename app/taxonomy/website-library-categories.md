# Website Library — Category System

## How this works
- Two mirrored roots in the vault: `Websites/` (bookmarked links) and `Notes/` (your own writing, reflections, and evaluations) — same category names under both, so a bookmark and your own note on the same topic never conflict or compete for the same folder.
- Maximum two folder levels: **Category → Subcategory**. Anything more specific becomes a tag instead of a folder.
- GitHub repo links (exact domain `github.com`) are excluded entirely from this system — your separate GitHub Project Curator app handles those. `GitHub Projects/` is reserved as its own top-level folder in the same vault.

## The domain law (owner, 2026-10-01 — v0.28.0)
**Banned from this Websites vault, always:** X/Twitter (`x.com`, `twitter.com`, `t.co`), the whole GitHub group (`github.com`, `gist.github.com`, `*.github.io`, `githubusercontent.com`), HuggingFace (`huggingface.co`, `hf.co`), Instagram, Facebook, and LinkedIn. Links on these domains are never fetched, never turned into notes, never retried — the `_inbox` platform tables and the bot's ledger keep the record. Any note that slips through is swept to `.trash/banned-domains` on the next run. This is law, not a setting: the "Blocked domains" field in Settings can only ADD domains.

---

## Categories

### Design 🌐 *(Assets & Resources and UI/UX & Product Design are public-directory candidates)*
- **Assets & Resources** — downloadable/usable design assets: icons, mockups, fonts, illustrations, UI kits, color/gradient tools
- **UI/UX & Product Design** — process & inspiration: galleries, design systems, UX writing, wireframing/prototyping
- **Print & Editorial Design** — brochures, catalogues, print templates *(empty for now — ready for future links)*
- **Branding & Identity** — logo tools, brand guidelines *(empty for now — ready for future links)*

Example tags: `#icons` `#mockup` `#illustration` `#vector` `#font` `#color-tool` `#gradient` `#ui-kit` `#free` `#paid` `#svg` `#3d-mockup` `#farsi-font` `#ai-powered` `#inspiration` `#design-system` `#ux-writing` `#wireframing` `#dark-mode` `#landing-page` `#mobile` `#dashboard` `#accessibility` `#brochure` `#catalogue` `#print-template` `#logo` `#brand-guideline`

### AI Tools for Web & App Development 🌐 *(public-directory candidate)*
- AI UI/Design Generation
- AI Coding Assistants
- AI Prototyping / No-Code

Example tags: `#ui-generation` `#code-gen` `#no-code`

### AI Tools (General)
- AI Writing & Content
- AI Research & Search Assistants
- AI Audio & Voice Generation

Example tags: `#tts` `#voice-cloning` `#research-ai`

### Developer Tools
*(no subcategories)*
Example tags: `#cli` `#testing` `#formatting` `#reference`

### Free Utilities & Everyday Tools
*(no subcategories)*
Example tags: `#pdf` `#converter` `#speed-test`

### Knowledge, Research & Reference
*(no subcategories)* — also the home for general, multi-topic learning platforms (Coursera, Udemy, Khan Academy). Tag with `#course`.
Example tags: `#data` `#academic` `#archive` `#course`

### Download Resources 🔒 *(the Torrent & Shadow Libraries subfolder is private)*
- **Public Archives & Libraries** — legitimate, public-domain (Gutenberg, Library of Congress, Internet Archive)
- **Torrent & Shadow Libraries** — Anna's Archive, libgen, sci-hub, Z-Library, torrent search engines

Example tags: `#ebook` `#torrent` `#shadow-library`

### Music & Audio Discovery
*(no subcategories)*
Example tags: `#radio` `#focus-music` `#sfx`

### Security & Privacy Tools
*(no subcategories)*
Example tags: `#breach-check` `#anon`

### Crypto & Markets
*(no subcategories)*
Example tags: `#tracker` `#exchange-rate`

### English Learning & Career Prep
General English-test prep (IELTS, etc.) and general career/salary research — **not** tied to a specific country. Country-specific relocation content goes under Living Abroad instead.
Example tags: `#ielts` `#immigration` `#salary`

### Geography & Urban Planning
*(empty for now — ready for future links)*
- Urban Planning Courses & Learning
- Maps, GIS & Data Tools
- Case Studies & Reference

Example tags: `#gis` `#zoning` `#transit` `#masterplan` `#course`

### Living Abroad
*(empty for now — ready for future links)*
Country-specific relocation content only: job boards, visa info, housing, cost of living for one particular country.
- Australia

Example tags: `#job-seeking` `#visa` `#housing` `#cost-of-living` `#salary`

### Random / Curiosities
Catch-all — use sparingly, only when nothing else genuinely fits.
*(no subcategories)*

---

## Judgment-call rules
*(these are also baked directly into `categorize_links.py`'s prompts, so the AI follows them automatically)*

- A single-purpose AI tool that does a design task (background removal, image upscaling, vectorizing) → **Design**, not either AI Tools category — its job is design, AI is just the mechanism. Tag `#ai-powered`.
- **AI Tools for Web & App Development** = tools that help *build* software (UI generation, code generation, no-code builders). **AI Tools (General)** = everything else AI-related with no build-a-product purpose.
- General course platforms (Coursera, Udemy) → **Knowledge, Research & Reference** + `#course`. A topic-specific course stays in its own topic folder + `#course`.
- Shadow libraries and piracy-adjacent sites → **Download Resources → Torrent & Shadow Libraries** (private). Legitimate public-domain archives → **Public Archives & Libraries**.

## Not yet covered
- ~~`*.github.io` sites and `gist.github.com` links~~ — **settled by the domain law (v0.28.0)**: the whole GitHub group is banned from the Websites vault; repo links keep going to the GitHub pipeline.
