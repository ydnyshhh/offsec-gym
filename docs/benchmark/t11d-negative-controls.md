# T1.1d A1 validator controls

`benchmark_t11d_conformance.py` tests the existing `DeterministicValidator` with controlled synthetic artifacts and an isolated hidden oracle. This is trusted conformance infrastructure, separate from the public-only Q4 policy. The positive fixture has complete same-run request, response, event, actor, object, and generation lineage. It demonstrates that the canonical validator can accept a complete A1 proof; it does not demonstrate public discovery.

| Control | Corruption or boundary | Required outcome |
| --- | --- | --- |
| NC1 | Missing cited evidence | Rejected |
| NC2 | Wrong document | Rejected |
| NC3 | Wrong actor | Rejected |
| NC4 | Wrong run | Rejected |
| NC5 | Wrong generation | Rejected |
| NC6 | Wrong action/evidence pair | Rejected |
| NC7 | Invalid response hash | Rejected |
| NC8 | Truncated evidence | Rejected |
| NC9 | Unsupported object relationship | Rejected |
| NC10 | Patched root | Rejected |
| NC11 | Unrelated property family | Rejected |
| NC12 | Duplicate accepted root | One distinct true positive |
| NC13 | Nonexistent resource | Rejected |
| NC14 | Missing canonical request event | Rejected |
| NC15 | Unavailable event stream | Inconclusive |

The tracked `t11d-validator-conformance.json` records expected and observed verdict classes and reason codes. Controlled fixture details remain in private output. NC15 preserves the distinction between infrastructure inconclusive and an ordinary security rejection.

Observed result: the positive fixture and all 15 controls passed under the pinned qualification source. This is an offline validator-conformance result.
