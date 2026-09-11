"""Stable algorithm seam for rebar artifact producers (not remeshing)."""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any, Mapping, Callable, Iterator

import numpy as np


class RebarAlgorithmError(ValueError):
    pass


class UnknownRebarAlgorithmError(RebarAlgorithmError):
    pass


@dataclass(frozen=True)
class RebarAnalysis:
    """Canonical result plus implementation-private details."""
    data: dict[str, Any]
    resources: Any = None


@dataclass(frozen=True)
class RebarInputContext:
    """Restartable raw-source reader. Indices refer to original reader records.

    Every yielded array is bounded; callers must not concatenate the raw cloud.
    Non-finite records are omitted without renumbering subsequent records.
    BIM geometry, when present, is already in the raw scan coordinate system.
    """
    sample: np.ndarray
    iter_chunks: Callable[[], Iterator[tuple[np.ndarray, np.ndarray]]]
    bim_prior: Mapping[str, Any] | None = None
    source_path: str | None = None


@dataclass(frozen=True)
class RebarPointAttributes:
    rebar_class: np.ndarray
    rebar_direction: np.ndarray
    rebar_instance: np.ndarray
    scene_class: np.ndarray | None = None
    rebar_flags: np.ndarray | None = None
    class_confidence: np.ndarray | None = None
    instance_confidence: np.ndarray | None = None
    fixture_kind: np.ndarray | None = None
    rebar_role: np.ndarray | None = None

    def validate(self, count: int) -> None:
        for name, value, dtype in (("REBAR_CLASS", self.rebar_class, np.uint8),
                                   ("REBAR_DIRECTION", self.rebar_direction, np.uint16),
                                   ("REBAR_INSTANCE", self.rebar_instance, np.uint32)):
            if not isinstance(value, np.ndarray) or value.shape != (count,) or value.dtype != np.dtype(dtype):
                raise RebarAlgorithmError(f"{name} must be a {dtype.__name__} array of length {count}")
        for name, value in (("SCENE_CLASS", self.scene_class), ("REBAR_FLAGS", self.rebar_flags),
                            ("FIXTURE_KIND", self.fixture_kind), ("REBAR_ROLE", self.rebar_role)):
            if value is not None and (not isinstance(value, np.ndarray) or value.shape != (count,) or value.dtype != np.dtype(np.uint8)):
                raise RebarAlgorithmError(f"{name} must be a uint8 array of length {count}")
        for name, value, maximum, scene in (("FIXTURE_KIND", self.fixture_kind, 3, 4),
                                            ("REBAR_ROLE", self.rebar_role, 2, 2)):
            if value is not None and (np.any(value > maximum) or
                    self.scene_class is None or np.any((value > 0) & (self.scene_class != scene))):
                raise RebarAlgorithmError(f"{name} must agree with its scene class and enum")
        for name, value in (("CLASS_CONFIDENCE", self.class_confidence), ("INSTANCE_CONFIDENCE", self.instance_confidence)):
            if value is not None and (not isinstance(value, np.ndarray) or value.shape != (count,) or value.dtype != np.float32 or not np.isfinite(value).all() or np.any((value < 0) | (value > 1))):
                raise RebarAlgorithmError(f"{name} must contain {count} finite float32 scores in [0,1]")


class RebarAlgorithm(ABC):
    @property
    @abstractmethod
    def descriptor(self) -> Mapping[str, Any]: ...

    @abstractmethod
    def normalize_parameters(self, raw: Mapping[str, Any] | None) -> Mapping[str, Any]: ...

    @abstractmethod
    def analyze(self, sample: np.ndarray, parameters: Mapping[str, Any]) -> RebarAnalysis: ...

    def analyze_source(self, context: RebarInputContext, parameters: Mapping[str, Any]) -> RebarAnalysis:
        return self.analyze(context.sample, parameters)

    @abstractmethod
    def project_points(self, points_xyz: np.ndarray, analysis: RebarAnalysis) -> RebarPointAttributes: ...


class RebarAlgorithmRegistry:
    def __init__(self) -> None:
        self._algorithms: dict[str, RebarAlgorithm] = {}

    def register(self, algorithm: RebarAlgorithm, *, replace: bool = False) -> None:
        identifier = str(algorithm.descriptor["id"])
        if identifier in self._algorithms and not replace:
            raise RebarAlgorithmError(f"rebar algorithm already registered: {identifier}")
        self._algorithms[identifier] = algorithm

    def unregister(self, identifier: str) -> RebarAlgorithm:
        try:
            return self._algorithms.pop(identifier)
        except KeyError as exc:
            raise UnknownRebarAlgorithmError(f"unknown rebar algorithm: {identifier}") from exc

    def has(self, identifier: str) -> bool:
        return identifier in self._algorithms

    def get(self, identifier: str) -> RebarAlgorithm:
        try:
            return self._algorithms[identifier]
        except KeyError as exc:
            raise UnknownRebarAlgorithmError(f"unknown rebar algorithm: {identifier}") from exc

    def descriptors(self) -> list[dict[str, Any]]:
        return [dict(self._algorithms[key].descriptor) for key in sorted(self._algorithms)]


REBAR_ALGORITHM_REGISTRY = RebarAlgorithmRegistry()
