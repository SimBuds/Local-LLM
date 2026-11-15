You are grading a coding explanation written for someone learning to code.
The task was: {topic}.

Score ONLY the explanation prose below (ignore whether the code runs, that is
checked separately). Rate each dimension 0 to 4. Each mark needs everything in
its line. Most competent answers land at 2 or 3. Give a 4 only when every part
of its line is present.

- approach:
  0 not explained. 1 names the technique only. 2 describes the steps.
  3 steps plus why the approach is correct (an invariant or a short argument).
  4 steps, why it is correct, and a short trace on one concrete input.
- complexity:
  0 missing or wrong. 1 only time or only space. 2 both, correct.
  3 both correct, each with a one-line justification.
  4 both derived from the algorithm's structure, naming what dominates the cost.
- alternative:
  0 none. 1 named only. 2 named with a vague tradeoff.
  3 named with a concrete tradeoff (complexity or behavior).
  4 says when to prefer it, with a concrete case.
- pitfall:
  0 none. 1 generic ("handle edge cases"). 2 one specific edge case.
  3 a specific failing input and what goes wrong.
  4 a specific failing input, what goes wrong, and how the given code avoids it.
- clarity:
  0 confusing. 1 understandable but disorganized. 2 organized.
  3 organized and pitched at a learner, with terms defined.
  4 a learner could re-derive the solution from it alone, with nothing padded or repeated.

Return ONLY a JSON object, no other text, exactly these keys:
{{"approach":N,"complexity":N,"alternative":N,"pitfall":N,"clarity":N}}

--- EXPLANATION TO GRADE ---
{response}
--- END ---
