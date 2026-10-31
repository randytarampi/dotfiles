## Review facilitation (facet: facilitation)

**Purpose:** Move existing review conversations toward a justified decision with minimal churn. This facet may be invoked alone to triage discussions; use enough code investigation to test claims without silently running a full new engineering sweep.

### Reconstruct and advance

1. Read the latest PR head, review submissions, inline threads, timeline, author replies and material changes. Distinguish a person's account from the provenance of comments posted through it; do not infer authorship from formatting.
2. Build a private or compact working **finding ledger**: ID, claim, evidence, severity, responsible owner, current disposition, and what would decide it. Link one technical concern to one discussion rather than duplicating bot comments.
3. Track dispositions: `open`, `fixed-and-verified`, `explained-and-accepted`, `reviewer-withdrew`, `deferred-to-owned-follow-up`, `owner-risk-accepted`, and `unresolved`. A reply, a changed line, a resolved GitHub thread or a green check alone proves none of these.
4. Revisit prior blockers after new commits; compare old and new behaviour, check for newly introduced risks, and revise the evidence and recommendation. Retract findings when stronger evidence disproves them.
5. Ask a precise, falsifiable question where the outcome depends on unavailable context; name an accountable role or owner **when known**, otherwise record the owner as unknown and request assignment. Do not invent names, dates or deadlines.
6. Record human or agent disagreements respectfully, with the evidence that would resolve them. If discussion stalls, escalate to the documented decision-maker; do not manufacture consensus or debate indefinitely.
7. Close a discussion only when verified or with a documented, authorized disposition. Risk acceptance requires the accountable owner's affirmative decision, scope and rationale; a review agent cannot grant it. Add an expiry or revisit trigger where appropriate.

### Evidence continuity

Bind conclusions to the inspected head commit. Recheck material findings after new pushes. A changed code range indicates a possible correction, not proof of resolution. Publish linked corrections for substantive retractions, while allowing edits to redact sensitive information. Assess other reviewers' claims on evidence rather than voting with their labels; verify decisive external sources when available and report inaccessible evidence.

### Compact publishing

Keep the complete ledger in working notes or a durable review artifact **when available**. Publish only new or materially changed findings, decisive evidence, remaining blockers and the current recommendation. Don't repost the forest and every thread on every pass. If nothing material changed, say so briefly or make no new comment.

Stop when decision-relevant findings are addressed or routed to an authorized decision-maker, or when new evidence is unavailable within the agreed stop budget. A subsequent push or author reply may reopen the review. Re-entry is a new invocation with fresh state, not an assumption of a continuously running agent.

### Provenance and multiple reviewers

Record publisher identity, disclosed/confirmed/unknown content provenance, and accountable decision-owner separately. Treat humans' and agents' claims by the same evidentiary standard. Several models running the same prompt may offer diverse perspectives, but don't count their agreement as independent verification without independent evidence and sufficiently distinct investigation.
