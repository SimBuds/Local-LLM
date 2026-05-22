# Google SEO Volatile Rules

**Review cadence:** quarterly. **Last reviewed:** 2026-05-21.

These rules track Google's *current* algorithmic posture (Helpful Content + E-E-A-T era, May 2026). They are expected to drift. Update this file when Google ships a major core update or when the AI-content guidance shifts. Do not move durable principles (E-E-A-T concept, honesty, burstiness) into this file — those live in `system.md`.

## Current Google framing

- Helpful Content System: rewards demonstrable first-hand experience, penalizes generic AI summaries.
- E-E-A-T weight is highest on YMYL (Your Money / Your Life) topics — health, finance, legal.
- AI-assisted content is permitted *if* it meets the same quality bar as human-written; mass-produced low-effort AI prose is explicitly demoted.
- **March 2026 core update:** Experience (first E in E-E-A-T) signal weighted above all others. Content from a named human author with first-hand specifics, verifiable credentials, and original outcomes outranks comprehensive impersonal content. Implication: every article needs an attributable author with real expertise.
- **Schema.org JSON-LD** (Article, FAQPage, HowTo) is now critical for AI-search citation, not just SERP. Treat structured-data output as table-stakes, not a nice-to-have.

## Negative constraint list (hard ban)

Never use these words or phrases. They are statistically correlated with low-effort AI prose as of May 2026:

- delve, delves, delving
- landscape (as metaphor: "the marketing landscape")
- testament (as in "a testament to")
- realm, tapestry, navigate (as metaphor), unleash, unlock (as metaphor)
- "in conclusion", "it is important to note", "it's worth noting"
- "in today's fast-paced world", "in the ever-evolving"
- "dive into", "deep dive"
- "game-changer", "game-changing", "revolutionize", "revolutionary"
- "leverage" (as verb meaning "use"), "synergy", "ecosystem" (outside biology/software)
- "robust", "seamless", "cutting-edge", "state-of-the-art" (as filler)

If the topic genuinely requires one of these terms (e.g., "ecosystem" in a biology article), use it once and only when no plainer word fits.

## Current structural emphases

- Front-load the answer in the first 100 words (Helpful Content rewards information gain over preamble).
- Match search intent precisely — informational queries get definitions and explainers; transactional queries get comparisons and recommendations.
- Avoid keyword stuffing; prioritize semantic neighbors and entity coverage over exact-match density.
