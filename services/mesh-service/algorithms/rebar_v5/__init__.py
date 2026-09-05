"""Independent staged, pure-point-cloud rebar segmentation V5 adapter."""
from dataclasses import asdict
import numpy as np
from threadpoolctl import threadpool_limits
from ..rebar_base import RebarAlgorithm, RebarInputContext
from .contracts import Params, VERSION, VISUALIZATION
from .pipeline import analyze, classify, export_sidecars, transfer_labels


class GeometricV5Adapter(RebarAlgorithm):
    @property
    def descriptor(self):
        defaults=asdict(Params())
        bounds={
            'feature_min_neighbors':{'minimum':3,'maximum':256},
            'feature_max_neighbors':{'minimum':3,'maximum':256},
            'neighbourhood_point_limit':{'maximum':2_000_000},
            'query_batch_size':{'maximum':8192},
            'detection_point_limit':{'maximum':1_000_000},
            'table_ransac_iterations':{'maximum':1000},
            'max_orientation_modes':{'maximum':128},
            'hough_angle_step_degrees':{'minimum':.25},
            'intersection_tolerance':{'maximum':.001},
            'intersection_min_angle_degrees':{'minimum':5,'exclusiveMaximum':90},
            'table_max_tilt_degrees':{'exclusiveMaximum':90},
            'planar_angle_degrees':{'exclusiveMaximum':90},
        }
        return {"id":"geometric-v5","version":VERSION,"name":"Geometric rebar v5", "analysisSchema":"rebar-analysis-v2",
            "capabilities":{"class":True,"direction":True,"instance":True,"confidence":True,"sceneClass":True,
                            "rebarFlags":True,"rawLabels":True,"features":True,"intersections":True,"bimPrior":False},
            "inputOptionSchema":{"type":"object","properties":{"maxInputPoints":{"type":"integer","default":200000,"minimum":3,"maximum":200000,"description":"Bootstrap sample only; all source records receive features and labels"},"voxelSize":{"type":"number","minimum":0.000001}}},
            "parameterSchema":{"type":"object","additionalProperties":False,"properties":{
                name:{"type":"integer" if isinstance(value,int) else "number","default":value,"minimum":1 if isinstance(value,int) else 0.000001,
                      **bounds.get(name,{}),
                      **({"unit":"m"} if any(token in name for token in ("radius","distance","gap","width","size","overlap","length","height","voxel","tolerance")) and "degrees" not in name and "ratio" not in name and "batch" not in name else {})}
                for name,value in defaults.items()}},"visualization":VISUALIZATION}

    def normalize_parameters(self,raw):
        return asdict(Params.from_value(raw))

    def analyze(self,sample,parameters):
        points=np.asarray(sample,dtype=np.float64)
        if points.ndim!=2 or points.shape[1:]!=(3,):raise ValueError("sample must contain XYZ records")
        finite=np.isfinite(points).all(axis=1)
        indices=np.flatnonzero(finite).astype(np.uint64)
        clean=points[finite]
        context=RebarInputContext(clean,lambda:iter([(indices,clean)]))
        return self.analyze_source(context,parameters)

    def analyze_source(self,context,parameters):
        if context.bim_prior is not None:raise ValueError("V5 uses point-cloud evidence only")
        # Rebar tasks are serialized by the mesh-service heavy-task gate.
        # Small PCA/RANSAC matrices lose time to BLAS thread fan-out; one BLAS
        # worker keeps runtime and peak workspace predictable. Restore on exit.
        with threadpool_limits(limits=1, user_api='blas'):
            return analyze(context,Params.from_value(parameters))

    def project_points(self,points_xyz,analysis):
        points=np.asarray(points_xyz,dtype=np.float64)
        if points.ndim!=2 or points.shape[1:]!=(3,) or not np.isfinite(points).all():raise ValueError("points must be finite XYZ")
        return (transfer_labels(points,analysis) if analysis.resources is not None else classify(points,analysis))[0]

    def project_candidates(self,points_xyz,analysis):
        points=np.asarray(points_xyz,dtype=np.float64)
        return (transfer_labels(points,analysis) if analysis.resources is not None else classify(points,analysis))[1]

    def export_sidecars(self,directory,analysis):
        return export_sidecars(directory,analysis)

    def close(self,analysis):
        if analysis.resources is not None:analysis.resources.close()
