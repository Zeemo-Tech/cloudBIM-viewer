from .base import RemeshAlgorithm, RemeshResult, ALGORITHM_REGISTRY
from .pymeshlab_remesh import PyMeshLabBIMPreprocessor, PyMeshLabBIMIsotropicOnly
from .rebar_base import REBAR_ALGORITHM_REGISTRY, RebarAlgorithm, RebarAnalysis, RebarPointAttributes
from .rebar_geometric import GeometricV2Adapter
from .rebar_geometric_v3 import GeometricV3Adapter
from .rebar_geometric_v4 import GeometricV4Adapter
from .rebar_v5 import GeometricV5Adapter

# Deliberately separate from ALGORITHM_REGISTRY (the remeshing registry).
REBAR_ALGORITHM_REGISTRY.register(GeometricV2Adapter(), replace=True)
REBAR_ALGORITHM_REGISTRY.register(GeometricV3Adapter(), replace=True)
REBAR_ALGORITHM_REGISTRY.register(GeometricV4Adapter(), replace=True)
REBAR_ALGORITHM_REGISTRY.register(GeometricV5Adapter(), replace=True)
