"""Stable algorithm seam for rebar artifact producers (not remeshing)."""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any, Mapping

import numpy as np


class RebarAlgorithmError(ValueError):
    pass


class UnknownRebarAlgorithmError(RebarAlgorithmError):
    pass


@dataclass(frozen=True)
class RebarAnalysis:
    """Canonical result plus implementation-private details."""
    data: dict[str, Any]


@dataclass(frozen=True)
class RebarPointAttributes:
    rebar_class: np.ndarray
    rebar_direction: np.ndarray
    rebar_instance: np.ndarray

    def validate(self, count: int) -> None:
        for name, value, dtype in (("REBAR_CLASS", self.rebar_class, np.uint8),
                                   ("REBAR_DIRECTION", self.rebar_direction, np.uint16),
                                   ("REBAR_INSTANCE", self.rebar_instance, np.uint32)):
            if not isinstance(value, np.ndarray) or value.shape != (count,) or value.dtype != np.dtype(dtype):
                raise RebarAlgorithmError(f"{name} must be a {dtype.__name__} array of length {count}")


class RebarAlgorithm(ABC):
    @property
    @abstractmethod
    def descriptor(self) -> Mapping[str, Any]: ...

    @abstractmethod
    def normalize_parameters(self, raw: Mapping[str, Any] | None) -> Mapping[str, Any]: ...

    @abstractmethod
    def analyze(self, sample: np.ndarray, parameters: Mapping[str, Any]) -> RebarAnalysis: ...

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
