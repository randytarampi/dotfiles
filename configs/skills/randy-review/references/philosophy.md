## Shared judgment heuristics (Randy-isms)

These are **defeasible heuristics**, not authority, mandatory vocabulary or shortcuts around evidence. When an example contradicts a heuristic, explain the exception.

- **Direction first:** Determine whether the architectural and product direction is sound before polishing implementation details. A correct implementation of a demonstrated wrong approach is a blocking concern.
- **Root cause, not symptoms:** Trace the cause and nearby affected surfaces proportionately; don't demand a repository-wide sweep for an unrelated concern.
- **"I'd rather it be consistently wrong, not half-right":** A partial migration or inconsistent contract can be harder to reason about than a uniform, known limitation. This does **not** justify knowingly unsafe behaviour. Evaluate whether to finish, narrow or defer the change.
- **"Looks good, doesn't work":** Persuasive descriptions, green CI and plausible generated configuration do not replace verification of the claimed behaviour. State what actually ran, what didn't and why.
- **"Not my problem" / hot potato:** Assign work to its legitimate accountable owner or explicitly record an unowned follow-up; don't silently absorb it or casually abandon a cross-boundary failure. `🔥🥔` is an informal mnemonic, not an appropriate substitute for a professional handoff.
- **"Stop optimizing [tiny cost]; take it out of my bank account":** When the straightforward solution costs little compared with engineering effort, complexity and operational risk, **pay the negligible charge** rather than optimizing it away. Quantify the trade-off where material; small unit costs may compound at scale.

Use technical lenses as needed: direction, root cause, partial migration, actual consumer, conventions, security, owner boundaries, long-term obligations, attribution, delegation and authorized override. Select lenses for the risk at hand; do not exhaustively apply every lens to every change.
