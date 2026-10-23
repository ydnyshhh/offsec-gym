"""Detached, no-model benchmark task contracts and qualification."""

from offsecgym.benchmark.manifests import make_task_manifest
from offsecgym.benchmark.registry import ARCHETYPES
from offsecgym.benchmark.tasks import BenchmarkTaskSpec, PublicTaskEnvelope

__all__ = ["ARCHETYPES", "BenchmarkTaskSpec", "PublicTaskEnvelope", "make_task_manifest"]
