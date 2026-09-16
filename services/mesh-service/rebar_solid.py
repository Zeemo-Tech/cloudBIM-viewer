"""Outward mesh normals and finite inward capture paths for one physical bar.

Tiles must be reassembled by source part before orienting them: an individual
tile can be an open patch. Only the query copy is welded; artifact vertex order,
positions and distance bindings never change.
"""
from __future__ import annotations

import numpy as np
import open3d as o3d
import trimesh


class RebarSolid:
    def __init__(self, meshes: list[trimesh.Trimesh], part_ids: list[str] | None = None):
        self.scene = o3d.t.geometry.RaycastingScene(nthreads=1)
        self.origin = np.concatenate([m.vertices for m in meshes]).mean(axis=0)
        self.epsilon = 1e-7  # 0.0001 mm, in recentered model coordinates
        self.diagnostics = {"closedPartCount": 0, "invalidPartCount": 0,
                            "flippedFaceCount": 0, "faceCount": 0,
                            "inwardCandidateCount": 0, "blockedCandidateCount": 0}
        groups: dict[str, list[trimesh.Trimesh]] = {}
        for mesh, part in zip(meshes, part_ids if part_ids is not None else ["bar"] * len(meshes)):
            groups.setdefault(part, []).append(mesh)
        for tiles in groups.values():
            original = np.concatenate([m.vertices for m in tiles])
            # Exact welding joins tile seams without collapsing thin geometry.
            xyz, inverse = np.unique(original, axis=0, return_inverse=True)
            faces, vertex_offset = [], 0
            for tile in tiles:
                faces.append(inverse[np.asarray(tile.faces) + vertex_offset])
                vertex_offset += len(tile.vertices)
            faces = np.concatenate(faces)
            solid = trimesh.Trimesh(xyz - self.origin, faces, process=False)
            before_normals = solid.face_normals.copy()
            solid.fix_normals(multibody=True)
            # Some trimesh versions retain the pre-inversion normal cache.
            # Reconstruct from repaired winding so all normals come from faces.
            solid = trimesh.Trimesh(np.asarray(solid.vertices).copy(),
                                    np.asarray(solid.faces).copy(), process=False)
            self.diagnostics["faceCount"] += len(faces)
            # Open/non-manifold/degenerate meshes cannot define a trusted solid.
            valid = (solid.is_watertight and solid.is_winding_consistent
                     and solid.volume > 0 and np.all(solid.area_faces > 0))
            self.diagnostics["closedPartCount" if valid else "invalidPartCount"] += 1
            flipped = np.einsum("ij,ij->i", before_normals, solid.face_normals) < 0
            self.diagnostics["flippedFaceCount"] += int(flipped.sum())
            vertex_offset, face_offset = 0, 0
            for tile in tiles:
                flip = flipped[face_offset:face_offset + len(tile.faces)]
                corrected = np.asarray(tile.faces).copy()
                corrected[flip] = corrected[flip, ::-1]
                tile.faces = corrected
                tile.vertex_normals = (solid.vertex_normals[inverse[vertex_offset:vertex_offset + len(tile.vertices)]]
                                       if valid else np.zeros((len(tile.vertices), 3)))
                vertex_offset += len(tile.vertices)
                face_offset += len(tile.faces)
            if valid:
                self.scene.add_triangles(o3d.core.Tensor(np.asarray(solid.vertices, np.float32)),
                                         o3d.core.Tensor(np.asarray(solid.faces, np.uint32)))

    def allows_inward(self, starts: np.ndarray, targets: np.ndarray) -> np.ndarray:
        """Allow only the first interior interval, including its exit surface.

        A test of the endpoint alone would accept a point inside a second limb
        after crossing the empty gap of a bent bar. Ray casting the whole path
        also handles oblique chords, caps and changing cross-sections.
        """
        self.diagnostics["inwardCandidateCount"] += len(starts)
        delta = targets - starts
        length = np.linalg.norm(delta, axis=1)
        direction = delta / np.maximum(length[:, None], 1e-30)
        rays = np.column_stack((starts - self.origin + self.epsilon * direction, direction))
        hit = self.scene.cast_rays(o3d.core.Tensor(rays.astype(np.float32)), nthreads=1)
        exit_distance = hit["t_hit"].numpy() + self.epsilon
        # A ray starting outside a sharp corner hits an entry face first. It
        # must not be mistaken for an interior segment of this surface sample.
        exit_facing = np.einsum("ij,ij->i", hit["primitive_normals"].numpy(), direction) > 0
        allowed = np.isfinite(exit_distance) & exit_facing & (length <= exit_distance + self.epsilon)
        allowed |= length <= self.epsilon
        self.diagnostics["blockedCandidateCount"] += int(np.count_nonzero(~allowed))
        return allowed
