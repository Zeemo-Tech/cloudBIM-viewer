"""Optional compiled geometry kernels; NumPy remains the reference fallback.

The Docker build compiles these kernels. Host tests can build with
``sh native/build.sh``; absence of a compiler/library never triggers a runtime
build. Input arrays stay owned by Python, and no native pointer is serialized.
"""
import ctypes
import os
from pathlib import Path
import weakref

import numpy as np

_LIBRARY = Path(__file__).parent/'native/libgeometry.so'
_lib = None
if _LIBRARY.is_file() and os.environ.get('CLOUDBIM_GEOMETRY_REFERENCE') != '1':
    _lib = ctypes.CDLL(str(_LIBRARY))
    _d = np.ctypeslib.ndpointer(dtype=np.float64, flags='C_CONTIGUOUS')
    _i = np.ctypeslib.ndpointer(dtype=np.int64, flags='C_CONTIGUOUS')
    _lib.surface_create.argtypes = [_d, _d, _d, _i, ctypes.c_int64, ctypes.c_int64]
    _lib.surface_create.restype = ctypes.c_void_p
    _lib.surface_destroy.argtypes = [ctypes.c_void_p]
    _lib.surface_destroy.restype = None
    _lib.surface_query.argtypes = [ctypes.c_void_p, _d, _d, ctypes.c_int64,
                                  ctypes.c_double, ctypes.c_int, _d, _i, _i]
    _lib.surface_query.restype = None
    _lib.surface_near.argtypes = [ctypes.c_void_p, _d, ctypes.c_int64, ctypes.c_double,
                                 ctypes.c_int, np.ctypeslib.ndpointer(dtype=np.uint8, flags='C_CONTIGUOUS')]
    _lib.surface_near.restype = None
    _lib.polyline_project.argtypes = [_d, ctypes.c_int64, _d, _d, _d, ctypes.c_int64, _d, _i, _d]
    _lib.polyline_project.restype = None
    _lib.polyline_objective.argtypes = _lib.polyline_project.argtypes
    _lib.polyline_objective.restype = None



def available():
    return _lib is not None


class SurfaceIndex:
    def __init__(self, triangles, normals, partition, valid):
        arrays = [np.ascontiguousarray(a, dtype=np.float64) for a in (triangles, normals, partition)]
        valid = np.ascontiguousarray(valid, dtype=np.int64)
        if (arrays[0].shape!=(len(triangles),3,3) or arrays[1].shape!=(len(triangles),3)
                or arrays[2].shape!=(len(triangles),3) or valid.ndim!=1
                or np.any(valid<0) or np.any(valid>=len(triangles))):
            raise ValueError('surface index arrays must be triangle aligned')
        self.pointer = _lib.surface_create(*arrays, valid, len(triangles), len(valid))
        if not self.pointer:
            raise MemoryError('could not build finite surface index')
        self._release = weakref.finalize(self, _lib.surface_destroy, self.pointer)

    @classmethod
    def broad_phase(cls, meshes):
        # Open3D queries float32 triangles. Bound those exact vertices, including
        # degenerate triangles, then retain a scale-aware rounding margin.
        triangles=np.concatenate([np.asarray(v,np.float32)[f].astype(np.float64) for v,f,_ in meshes])
        zero=np.zeros((len(triangles),3))
        result=cls(triangles,zero,zero,np.arange(len(triangles),dtype=np.int64))
        result.roundoff=32*np.finfo(np.float32).eps*max(1.,float(np.abs(triangles).max()))
        return result

    def near(self, points, tolerance, workers):
        points=np.ascontiguousarray(points,dtype=np.float64)
        if points.ndim!=2 or points.shape[1]!=3:raise ValueError('points must have shape (N, 3)')
        near=np.empty(len(points),np.uint8)
        _lib.surface_near(self.pointer,points,len(points),float(tolerance+self.roundoff),
                          max(1,min(8,int(workers))),near)
        return near.astype(bool)

    def query(self, points, normals, tolerance, workers):
        points = np.ascontiguousarray(points, dtype=np.float64)
        normals = np.ascontiguousarray(normals, dtype=np.float64)
        if points.ndim!=2 or points.shape[1]!=3 or normals.shape!=points.shape:
            raise ValueError('query points and normals must have matching shape (N, 3)')
        distance = np.empty(len(points), np.float64)
        chosen = np.empty(len(points), np.int64)
        operations = np.empty(len(points), np.int64)
        _lib.surface_query(self.pointer, points, normals, len(points), float(tolerance),
                           max(1, min(8, int(workers))), distance, chosen, operations)
        return distance, chosen, int(operations.sum())


def project(points, starts, delta, squares, *, expanded=False):
    points, starts, delta, squares = [np.ascontiguousarray(a, dtype=np.float64)
                                     for a in (points, starts, delta, squares)]
    if (points.ndim!=2 or points.shape[1]!=3 or starts.ndim!=2 or starts.shape[1]!=3
            or delta.shape!=starts.shape or squares.shape!=(len(starts),)
            or not len(starts) or np.any(squares<=0)):
        raise ValueError('finite projection requires aligned positive-length segments')
    best = np.empty(len(points), np.float64)
    nearest = np.empty(len(points), np.int64)
    fractions = np.empty(len(points), np.float64)
    function = _lib.polyline_objective if expanded else _lib.polyline_project
    function(points, len(points), starts, delta, squares, len(starts), best, nearest, fractions)
    return best, nearest, fractions
