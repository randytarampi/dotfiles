# Randy review method — evaluation and interpretation

This document is **not part of the runtime review prompt**. It records how to challenge and measure the method rather than telling every reviewer to conduct a historical study.

## Reported 23-bundle re-validation (30 October 2026)

A colleague reported 46 successful subagent runs and 20/23 passing PR bundles (87%). They reported process-faithfulness mean 4.44 with 15/16 threaded cases at or above the target, relational mechanics exercised in 14/16, and only 1/4 contaminated-provenance cases passing. These are **colleague-reported results**, not independently reproduced by this PR. Same-family executor/judge and small stratum sizes limit the inference.

Do **not** reinterpret 46 subagent runs as 46 independent PR cases, nor infer that exercising a behaviour makes it correct. The contaminated failures are important regression cases, including false attribution and missed provenance disclosures.

## Recommended evaluation

Keep a frozen set of held-out historical PR/issue conversations, with provenance, original comments, commits, actual outcomes and observed technical evidence. Test at decision checkpoints without revealing subsequent conversation. Exclude training examples from held-out tests.

Score technical correctness, false positives, response to counterevidence, finding lifecycle, provenance precision/recall, authorization boundaries, unnecessary comment volume and cost to reach defensible closure. Distinguish historical resemblance from correct engineering judgment: a model should be allowed to disagree with Randy when the evidence supports it.

Evaluate three single facets, all three pairs and all three together, including different models/vendors using the **same composed prompt**. For purported independent reviewers, log model, source context, tool access, evidence and actual independence. Cross-vendor agreement is not a ground truth label.

Preserve failed cases and negative controls, especially malicious diff instructions, incorrect bot provenance, unverified runtime claims, invalid risk acceptance and reviewers who retract a mistaken blocker. Do not tune repeatedly on the planned 50-PR external validation set; record its selection and rubric before examining results.

A review method is validated by observable decisions and outcomes, not by claiming to reproduce another person's private reasoning.
