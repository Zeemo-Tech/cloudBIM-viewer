"""Versioned workbench-only evidence contracts; support scores are not probabilities."""
from dataclasses import dataclass, field, asdict
from enum import IntEnum
import numpy as np

VERSION = 'design-evidence-v1'
MODES = ('off', 'design-evidence')
ATTRIBUTES = {'review_state': 'u1', 'review_reason': '<u2', 'review_changed': 'u1'}

class State(IntEnum):
    UNREVIEWED = 0
    CONFIRMED = 1
    PROVISIONAL = 2
    INSUFFICIENT = 3
    REJECTED_OBSERVED = 4
    EXCLUDED_BOUNDARY = 5

class Reason(IntEnum):
    NONE = 0
    CYLINDER_SUPPORT = 1
    AMBIGUOUS_OWNER = 2
    SPARSE_SUPPORT = 3
    MISSING_NORMALS = 4
    PARTIAL_ARC = 5
    PLANE_WINS = 6
    SURFACE_MISMATCH = 7
    GEOMETRY_CONFLICT = 8
    HARD_BOUNDARY = 9
    NO_NEW_SUPPORT = 10
    TOPOLOGY_CONFLICT = 11
    MISSING_ENDPOINTS = 12
    DENOISING_COUNTEREVIDENCE = 13

@dataclass(frozen=True)
class RobustnessPolicy:
    enable_candidates: bool = True
    enable_reclassification: bool = True
    enable_topology_retry: bool = True
    voxel_size: float = .002
    window_margin: float = .018
    endpoint_margin: float = .025
    max_offset: float = .04
    max_angle_degrees: float = 12.
    max_surface_error: float = .0015
    min_occupied_cells: int = 12
    min_occupied_bins: int = 3
    axial_bin: float = .005
    minimum_span: float = .020
    min_radial_alignment: float = .70
    min_arc_degrees: float = 20.
    confirmed_arc_degrees: float = 60.
    plane_advantage: float = .80
    plane_normal_alignment: float = .995
    min_planar_patch_fraction: float = .20
    match_margin: float = .12
    max_retries: int = 2
    retry_scale: float = 1.5
    max_fit_points: int = 2048
    max_unit_candidates: int = 8
    short_layer_tolerance: float = .01

@dataclass(frozen=True)
class AcceptancePolicy:
    length_absolute_m: float = .020
    length_relative: float = .05
    position_m: float = .010
    angle_degrees: float = 5.
    diameter_m: float = .0015
    layer_m: float = .010
    max_time_ratio: float = 2.

@dataclass
class Evidence:
    state: State
    reason: Reason
    positive: tuple = ()
    negative: tuple = ()
    missing: tuple = ()
    score: float = 0.
    @property
    def accepted(self):
        return self.state in (State.CONFIRMED, State.PROVISIONAL)
    @property
    def anchor_eligible(self):
        return self.state == State.CONFIRMED
    def report(self):
        return {**asdict(self), 'state': self.state.name.lower(), 'reason': self.reason.name.lower(),
                'scoreMeaning': 'measured support ranking, not calibrated probability'}

@dataclass
class Candidate:
    unit_id: str
    rows: np.ndarray
    model: dict
    metrics: dict
    attempt: int = 0
    evidence: Evidence | None = None
    provenance: dict = field(default_factory=dict)
