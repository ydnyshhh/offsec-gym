"""Typed attack-graph opportunities and variant-specific exploit outcomes."""

from __future__ import annotations

from typing import Literal
from uuid import UUID

from pydantic import Field, field_validator, model_validator

from offsecgym.schemas.common import StrictModel
from offsecgym.schemas.ground_truth import GroundTruthManifest


class AttackGraphNode(StrictModel):
    node_id: UUID
    slug: str = Field(min_length=1)

    @field_validator("node_id")
    @classmethod
    def deterministic_id(cls, value: UUID) -> UUID:
        if value.version != 5:
            raise ValueError("node ID must be deterministic UUIDv5")
        return value


class AttackGraphEdge(StrictModel):
    edge_id: UUID
    from_node: UUID
    to_node: UUID
    prerequisites: tuple[UUID, ...] = ()
    action_family: str = Field(min_length=1)
    property_id: UUID | None = None
    required: bool = False
    kind: Literal["progress", "boundary_test", "violation"]
    active: bool = True
    expected_outcome: Literal["observed", "blocked", "succeeded"]
    evidence_requirements: tuple[str, ...] = ()

    @field_validator("edge_id")
    @classmethod
    def deterministic_id(cls, value: UUID) -> UUID:
        if value.version != 5:
            raise ValueError("edge ID must be deterministic UUIDv5")
        return value

    @model_validator(mode="after")
    def validate_edge_semantics(self) -> AttackGraphEdge:
        if self.kind in {"boundary_test", "violation"} and self.property_id is None:
            raise ValueError("security boundary edge needs property ID")
        if self.kind == "boundary_test" and not self.active:
            raise ValueError("security boundary tests remain active in every variant")
        if self.kind == "violation" and self.expected_outcome != (
            "succeeded" if self.active else "blocked"
        ):
            raise ValueError("violation outcome must reflect activation")
        if not self.active and self.required:
            raise ValueError("inactive edge cannot be required")
        if any(not item for item in self.evidence_requirements):
            raise ValueError("empty evidence requirement")
        return self


class AttackGraphManifest(StrictModel):
    schema_version: Literal["2"] = "2"
    scenario_id: str = Field(min_length=1)
    build_id: UUID
    pair_id: UUID
    seed: int = Field(ge=0)
    variant: Literal["vulnerable", "patched", "selective"]
    nodes: tuple[AttackGraphNode, ...] = Field(min_length=1)
    edges: tuple[AttackGraphEdge, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_graph(self) -> AttackGraphManifest:
        node_ids = {node.node_id for node in self.nodes}
        edge_ids = {edge.edge_id for edge in self.edges}
        if len(node_ids) != len(self.nodes) or len({node.slug for node in self.nodes}) != len(
            self.nodes
        ):
            raise ValueError("duplicate attack-graph node")
        if len(edge_ids) != len(self.edges):
            raise ValueError("duplicate attack-graph edge")
        for edge in self.edges:
            if edge.from_node not in node_ids or edge.to_node not in node_ids:
                raise ValueError("attack-graph edge references unknown node")
            if any(node not in node_ids for node in edge.prerequisites):
                raise ValueError("attack-graph edge references unknown prerequisite")
        return self

    def validate_against_ground_truth(self, ground_truth: GroundTruthManifest) -> None:
        """Validate cross-manifest binding and every security-property reference."""
        if (
            self.scenario_id,
            self.build_id,
            self.pair_id,
            self.seed,
            self.variant,
        ) != (
            ground_truth.scenario_id,
            ground_truth.build_id,
            ground_truth.pair_id,
            ground_truth.seed,
            ground_truth.variant,
        ):
            raise ValueError("attack graph and ground truth describe different builds")
        properties = {prop.property_id: prop for prop in ground_truth.properties}
        referenced: set[UUID] = set()
        for edge in self.edges:
            if edge.property_id is None:
                continue
            prop = properties.get(edge.property_id)
            if prop is None:
                raise ValueError("attack-graph edge references unknown property")
            referenced.add(edge.property_id)
            if edge.kind == "violation" and edge.active != prop.active:
                raise ValueError("violation edge activation differs from oracle property")
        if referenced != properties.keys():
            raise ValueError("ground-truth property has no attack-graph edge")
