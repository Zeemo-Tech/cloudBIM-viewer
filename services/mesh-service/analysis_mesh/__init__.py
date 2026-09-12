from .rebar_adapter import RebarSweepComponent
from .contracts import AlgorithmRegistry

registry = AlgorithmRegistry()
registry.register(RebarSweepComponent())

__all__ = ["registry"]
