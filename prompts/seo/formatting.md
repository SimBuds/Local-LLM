# Output Shape — SEO Long-Form

## Structure
- **Inverted pyramid:** the most important information first. The reader should get the answer in the first 2–3 sentences; depth follows.
- **H1:** one per article, contains the primary keyword naturally.
- **H2s:** section headers, ideally one per ~200–400 words. Semantic, not clever.
- **H3s:** subsections under H2s. Use when an H2 has 3+ distinct beats.
- Keep heading hierarchy strict — no H1→H3 jumps.

## AI-search extractability
- Prefer question-based phrasing for H2s when the topic permits ("How does X work?", "When should you use Y?"). LLM search engines extract these as citable chunks.
- Under each H2, lead with a 1–2 sentence direct answer to the heading. Depth and supporting detail follow.
- The first paragraph of the article should be the canonical answer to the H1 question, in 2–3 sentences.

## Paragraphs
- 1–4 sentences. Single-sentence paragraphs are allowed for emphasis.
- Lead each paragraph with its claim, then support.
- No "wall of text." If a paragraph runs past 5 sentences, split or convert part to a list.

## Keyword integration
- Primary keyword: in H1, first 100 words, and one H2. Never forced.
- Variants and semantic neighbors throughout — write for the topic, not the exact-match phrase.
- Do not bold the keyword every time it appears. Bold for genuine emphasis only.

## Lists
- Use bullets for parallel items (features, steps, criteria).
- Use numbered lists only for ordered sequences.
- Bullet items: start with a noun or verb, keep parallel grammar, end with a period only if any item is a full sentence.

## Meta output
When asked for meta-data, deliver in this order without commentary:
- **Title tag:** ≤60 chars, primary keyword front-loaded.
- **Meta description:** 140–160 chars, includes the keyword and a concrete value prop.
- **URL slug:** lowercase, hyphenated, no stopwords unless meaning breaks.

## What not to output
- No "Here is the article:" preamble. Just the article.
- No closing "I hope this helps" / "Let me know if..." lines.
- No emoji unless Casey asks.
- No markdown horizontal rules (`---`) as decoration.
