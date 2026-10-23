"""Pure historical acceptance replay joined to an independently pinned audit."""

from __future__ import annotations

import re
from collections import Counter

from offsecgym.research.m683_reference_contracts import CONTRACT_IDS, compare_contracts

HISTORICAL_INPUT_SHA256 = "34bc9b4a9348fcf3a324ea7265a6e8d53d076e5d8e96778354bee1b008093647"
GROUNDING_DETAIL_SHA256 = "c5100b5f26a6c3be53a14963dec92a09f984130d63cce005099dcce65dc659b5"
GROUNDING_AGGREGATE_SHA256 = "d859370459c843d726d82831a763f99035dfd94c7152334e545067483db53779"
FROZEN_PARSER_SHA256 = "f9b81420771e6e8802682aa5dd6383165847f705295f3a15883fc2dae23951d5"
_FRAGMENT = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", re.I)


def _tristate(value: object) -> str:
    return "true" if value is True else "false" if value is False else "unknown"


def replay(
    historical: object, grounding: object, *, require_historical_counts: bool = True
) -> tuple[dict[str, object], list[dict[str, object]]]:
    """Compare exact saved proposals; grounding never influences validation."""
    if not isinstance(historical, list) or not isinstance(grounding, list):
        raise ValueError("historical and grounding inputs must be row arrays")
    if len(historical) != len(grounding) or (require_historical_counts and len(historical) != 18):
        raise ValueError("pinned proposal cohort differs")
    references_by_contract = {contract: Counter() for contract in CONTRACT_IDS}
    proposals_by_contract = {contract: Counter() for contract in CONTRACT_IDS}
    grounding_by_contract = {contract: Counter() for contract in CONTRACT_IDS}
    marginal_references: Counter[str] = Counter()
    marginal_proposals: Counter[str] = Counter()
    private: list[dict[str, object]] = []
    reference_count = 0
    for ordinal, (row, grounded_row) in enumerate(zip(historical, grounding, strict=True)):
        if not isinstance(row, dict) or not isinstance(grounded_row, dict):
            raise ValueError("malformed historical row")
        arguments = row.get("arguments")
        source_refs = row.get("references")
        ground_refs = grounded_row.get("references")
        targets = arguments.get("target_objects") if isinstance(arguments, dict) else None
        if (
            not isinstance(targets, list)
            or not isinstance(source_refs, list)
            or not isinstance(ground_refs, list)
            or len(targets) != len(source_refs)
            or len(targets) != len(ground_refs)
            or grounded_row.get("ordinal") != ordinal
            or grounded_row.get("tool_call_id") != row.get("tool_call_id")
        ):
            raise ValueError("historical and grounding reference alignment differs")
        for value, source, ground in zip(targets, source_refs, ground_refs, strict=True):
            if (
                not isinstance(source, dict)
                or not isinstance(ground, dict)
                or source.get("original_value") != value
                or ground.get("raw_reference") != value
            ):
                raise ValueError("historical and grounding reference bytes differ")
        results = compare_contracts(arguments)
        reference_count += len(targets)
        private_refs: list[dict[str, object]] = []
        for index, (value, ground) in enumerate(zip(targets, ground_refs, strict=True)):
            per_contract = {
                contract: results[contract].references[index] for contract in CONTRACT_IDS
            }
            fragments = _FRAGMENT.findall(value) if isinstance(value, str) else []
            identifier = ground["normalized_identifier"]
            edges = grounded_row["public_relationship_edges"]
            involved = (
                any(
                    identifier in {edge["source_identifier"], edge["target_identifier"]}
                    for edge in edges
                )
                if identifier is not None
                else False
            )
            for contract, result in per_contract.items():
                counts = references_by_contract[contract]
                counts["accepted" if result.accepted else "rejected"] += 1
                counts[
                    result.form if result.accepted else result.error_category or "other_error"
                ] += 1
                strat = grounding_by_contract[contract]
                status = _tristate(ground["public_source_grounded"])
                strat[f"{'accepted' if result.accepted else 'rejected'}:{status}"] += 1
                if result.accepted and ground["object_resolution_status"] == "unique_typed":
                    strat["accepted:unique_public_entity"] += 1
                if result.accepted:
                    strat[f"accepted:declared_kind_match:{_tristate(ground['kind_match'])}"] += 1
                if contract in {"C1", "C2"} and result.accepted:
                    if len(fragments) != 1 or result.canonical_uuid != fragments[0]:
                        raise ValueError("normalization changed a model-authored UUID")
            if per_contract["C1"].accepted and not per_contract["C2"].accepted:
                raise ValueError("C2 rejected an existing C1 form")
            newly = not per_contract["C1"].accepted and per_contract["C2"].accepted
            if newly:
                alias = per_contract["C2"]
                if (
                    alias.form != "proposed_alias"
                    or alias.declared_kind != "change"
                    or alias.canonical_kind != "change_request"
                    or len(fragments) != 1
                    or alias.canonical_uuid != fragments[0]
                ):
                    raise ValueError("C2 admitted an unprespecified or changed reference")
                marginal_references["total"] += 1
                marginal_references[f"grounded:{_tristate(ground['public_source_grounded'])}"] += 1
                marginal_references[f"kind_match:{_tristate(ground['kind_match'])}"] += 1
                marginal_references[f"public_entity:{ground['object_resolution_status']}"] += 1
                oracle = ground["offline_oracle_correspondence"]
                marginal_references[
                    f"oracle_root_match:{_tristate(oracle['target_root_match'])}"
                ] += 1
                marginal_references["public_relationship_involving_reference"] += involved
            private_refs.append(
                {
                    "index": index,
                    "original_reference": value,
                    "grounding": {
                        "public_source_grounded": ground["public_source_grounded"],
                        "public_entity_resolved": ground["object_resolution_status"],
                        "declared_kind_matches_public_kind": ground["kind_match"],
                        "public_relationship_established": True if involved else None,
                        "oracle_target_root_match": ground["offline_oracle_correspondence"][
                            "target_root_match"
                        ],
                    },
                    "contracts": {
                        contract: {
                            "accepted": result.accepted,
                            "form": result.form,
                            "error_category": result.error_category,
                            "canonical_uuid": result.canonical_uuid,
                            "declared_kind": result.declared_kind,
                            "canonical_kind": result.canonical_kind,
                        }
                        for contract, result in per_contract.items()
                    },
                }
            )
        all_grounded = all(ref["public_source_grounded"] is True for ref in ground_refs)
        any_grounded = any(ref["public_source_grounded"] is True for ref in ground_refs)
        for contract, result in results.items():
            count = proposals_by_contract[contract]
            accepted = sum(ref.accepted for ref in result.references)
            count["all_references_accepted"] += result.syntax_accepted
            count["eligible_and_schema_valid"] += result.post_normalization_schema_accepted
            count["any_reference_rejected"] += not result.syntax_accepted
            count["mixed_accepted_and_rejected"] += 0 < accepted < len(targets)
            count["other_schema_failure"] += (
                result.syntax_accepted and not result.post_normalization_schema_accepted
            )
            count["eligible_and_all_publicly_grounded"] += (
                result.post_normalization_schema_accepted and all_grounded
            )
            count["at_least_one_grounded_but_syntax_rejected"] += (
                any_grounded and not result.syntax_accepted
            )
        if (
            results["C1"].post_normalization_schema_accepted
            and not results["C2"].post_normalization_schema_accepted
        ):
            raise ValueError("C2 lost a C1-eligible proposal")
        newly_eligible = (
            not results["C1"].post_normalization_schema_accepted
            and results["C2"].post_normalization_schema_accepted
        )
        if newly_eligible:
            marginal_proposals["total"] += 1
            marginal_proposals[
                f"all_grounded:{_tristate(grounded_row['all_references_grounded'])}"
            ] += 1
            marginal_proposals["all_correctly_typed"] += (
                grounded_row["all_references_correctly_typed"] is True
            )
        private.append(
            {
                "ordinal": ordinal,
                "tool_call_id": row["tool_call_id"],
                "reference_count": len(targets),
                "newly_eligible_C2": newly_eligible,
                "contracts": {
                    contract: {
                        "syntax_accepted": result.syntax_accepted,
                        "schema_accepted": result.post_normalization_schema_accepted,
                        "failure_category": (
                            "accepted"
                            if result.post_normalization_schema_accepted
                            else "reference:"
                            + ",".join(
                                sorted(
                                    {
                                        ref.error_category or "other_representation_error"
                                        for ref in result.references
                                        if not ref.accepted
                                    }
                                )
                            )
                            if not result.syntax_accepted
                            else result.schema_error or "other_schema_failure"
                        ),
                        "normalization_error": result.normalization_error,
                        "schema_error": result.schema_error,
                        "normalized_target_uuids": result.normalized_target_uuids,
                    }
                    for contract, result in results.items()
                },
                "references": private_refs,
            }
        )
    if require_historical_counts and reference_count != 43:
        raise ValueError("pinned reference cohort differs")
    if require_historical_counts and (
        proposals_by_contract["C0"]["eligible_and_schema_valid"] != 0
        or references_by_contract["C1"]["accepted"] != 27
        or proposals_by_contract["C1"]["eligible_and_schema_valid"] != 4
    ):
        raise ValueError("frozen C0/C1 replay results differ")
    public = {
        "milestone": "M6.8.3",
        "scope": "detached_reference_contract_comparison",
        "cohort": {"proposals": len(historical), "references": reference_count},
        "contracts": {
            contract: {
                "reference_level": dict(sorted(references_by_contract[contract].items())),
                "proposal_level": dict(sorted(proposals_by_contract[contract].items())),
                "grounding_stratification": dict(sorted(grounding_by_contract[contract].items())),
            }
            for contract in CONTRACT_IDS
        },
        "C1_to_C2_marginal_references": dict(sorted(marginal_references.items())),
        "C1_to_C2_marginal_proposals": dict(sorted(marginal_proposals.items())),
        "identity_preservation": {
            "accepted_C1_C2_reference_uuids_unchanged": True,
            "C2_only_adds_prespecified_alias": True,
            "whole_arrays_preserved": True,
            "references_dropped_substituted_or_invented": 0,
        },
        "prospective_effect_estimated": False,
        "live_autonomous_agent_treatment_qualified": False,
        "provider_calls": 0,
        "historical_outcomes_modified": False,
    }
    return public, private
