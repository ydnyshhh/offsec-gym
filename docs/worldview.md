# Structured worldview

The worldview is a queryable projection of assets, services, endpoints, identities, objects,
relationships, observations, hypotheses, findings, coverage, and open questions. Every fact
has a stable ID, typed bounded value, source worker/event/action/evidence IDs, confidence,
and timestamp. Facts can point to contradictions and superseded facts. Allowed states are
hypothesized, observed,
corroborated, validated, contradicted, and superseded.

Worker submissions enter as claims. Adjudication checks provenance and contradictions
before changing shared status. Context construction retrieves facts relevant to a task;
it does not append the entire run transcript. Later ablations can replace this context
policy while holding the range, model, tools, and budget fixed. Milestone 2.5 implements the
`WorldFact` contract only; WorldState storage, retrieval, and adjudication are future work.
