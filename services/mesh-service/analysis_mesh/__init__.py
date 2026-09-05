from .adapter import PyMeshLabIsotropicComponent
from .contracts import AlgorithmRegistry

registry = AlgorithmRegistry()
registry.register(PyMeshLabIsotropicComponent())

__all__ = ["registry"]
