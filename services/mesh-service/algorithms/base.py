from __future__ import annotations

import abc
from dataclasses import dataclass, field
from typing import Any


@dataclass
class RemeshResult:
    """网格处理结果。"""
    output_path: str
    vertex_count_before: int = 0
    face_count_before: int = 0
    vertex_count_after: int = 0
    face_count_after: int = 0
    extra: dict[str, Any] = field(default_factory=dict)


class RemeshAlgorithm(abc.ABC):
    """网格处理算法抽象基类。"""

    name: str = ""
    label: str = ""

    @abc.abstractmethod
    def run(self, input_path: str, output_path: str, params: dict[str, Any]) -> RemeshResult:
        ...

    def describe_params(self) -> list[dict]:
        """返回该算法可接受的参数说明列表，供前端动态渲染。"""
        return []


ALGORITHM_REGISTRY: dict[str, type[RemeshAlgorithm]] = {}


def register(cls: type[RemeshAlgorithm]) -> type[RemeshAlgorithm]:
    ALGORITHM_REGISTRY[cls.name] = cls
    return cls
