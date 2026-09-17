"""Export original LAS records and preview exactly the same source rows."""
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
import laspy
import numpy as np
from pointcloud_step_pipeline import write_las, write_preview


class CylinderArtifactTests(unittest.TestCase):
    def test_las_subset_and_preview_preserve_source_identity(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            las = laspy.LasData(laspy.LasHeader(point_format=3, version='1.2'))
            las.x = np.arange(6, dtype=float); las.y = np.zeros(6); las.z = np.zeros(6)
            las.intensity = np.arange(10, 16, dtype=np.uint16)
            source = root / 'source.las'; las.write(source)
            zeros = np.zeros(6, dtype=np.uint8)
            context = SimpleNamespace(positions=np.column_stack((las.x, las.y, las.z)),
                normals=np.tile([0, 1, 0], (6, 1)), normal_valid=np.ones(6, dtype='u1'),
                curvature=np.zeros(6), neighbor_radius=np.ones(6),
                shared_table_mask=None, geometry_class=None, projection_class=None,
                fused_class=np.array([3, 3, 3, 3, 4, 2], dtype='u1'),
                fused_region=zeros, fused_recovered=zeros, fused_reason=zeros,
                fused_steel_score=np.ones(6), fused_steel_evidence=zeros,
                refined_class=None, internal_type=None,
                complete_class=np.array([3, 3, 3, 3, 4, 2], dtype='u1'),
                complete_instance=np.array([7, 7, 19, 19, 0, 0], dtype='<u4'),
                complete_segment=np.array([1, 1, 2, 2, 0, 0], dtype='<u4'),
                complete_cluster=np.array([1, 1, 2, 2, 0, 0], dtype='<u4'),
                complete_confidence=np.ones(6),
                cylinder_keep=np.array([1, 0, 1, 1, 0, 0], dtype='u1'),
                cylinder_removed=np.array([0, 1, 0, 0, 0, 0], dtype='u1'))
            write_las(source, root / 'full.las', context,
                      {'cylinder-steel': root / 'clean.las', 'cylinder-removed': root / 'removed.las'})
            clean, removed = laspy.read(root / 'clean.las'), laspy.read(root / 'removed.las')
            np.testing.assert_array_equal(clean.source_record_index, [0, 2, 3])
            np.testing.assert_array_equal(clean.complete_instance, [7, 19, 19])
            np.testing.assert_array_equal(clean.intensity, [10, 12, 13])
            np.testing.assert_array_equal(removed.source_record_index, [1])
            self.assertEqual(len(laspy.read(root / 'full.las').points), 6)
            preview = write_preview(root, context, np.zeros((6, 3), dtype='u1'), 'run', 3)
            ids = np.fromfile(root / 'preview/source_indices.bin', dtype='<u8')
            for name in ('cylinder_keep', 'cylinder_removed'):
                self.assertIn(name + 'Url', preview)
                np.testing.assert_array_equal(np.fromfile(root / f'preview/{name}.bin', dtype='u1'), getattr(context, name)[ids])

if __name__ == '__main__':
    unittest.main()
