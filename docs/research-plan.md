# Research plan

The first study tests model-by-orchestrator interactions in autonomous security work.
Establish a scripted oracle and one vulnerable/patched security-property pair first.
Then expand the SaaS range, add a monolithic baseline, structured state, and ephemeral
workers. Use 10–20 diagnostic LLM runs to find harness bugs before drawing conclusions.

The initial comparative suite is 10 seeds × 3 orchestrators × 2 models × 3 replications
= 180 runs. Advance to the proposed 720-run study only after a pilot estimates variance,
inference cost, and infrastructure failure rate. Keep a held-out seed/variant set.

Memory representation and worker lifetime are separate experimental factors. Changing
both in one condition does not identify either effect. Validator comparisons reuse the
same candidates. Counterfactual pairs differ only in the specified security condition.
Report confidence intervals and interaction effects, not only aggregate solve rates.
