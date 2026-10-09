from pathlib import Path
import json
import tempfile
import unittest
import laspy
import numpy as np
from pointcloud_segmentation_preview import publish_preview
from pointcloud_tile_sidecar import pnts_positions
from test_rebar_tiles import _write, _read

class SegmentationPreviewTests(unittest.TestCase):
    def inputs(self, root):
        xyz = np.array([[0, 0, 0], [1, 0, 0], [0, -1, 0], [1, -1, -.1]], float)
        transform = np.diag([1., -1., -1., 1.])
        cloud = laspy.LasData(laspy.LasHeader(point_format=3, version='1.2'))
        cloud.xyz = xyz
        cloud.write(root / 'source.las')
        tiles, run = root / 'tiles', root / 'run'
        tiles.mkdir(); run.mkdir()
        _write(tiles / 'content.pnts', {'POINTS_LENGTH': 4, 'POSITION': {'byteOffset': 0}}, xyz.astype('<f4').tobytes())
        (tiles / 'tileset.json').write_text(json.dumps({'asset': {'version': '1.0'}, 'root': {'content': {'uri': 'content.pnts'}}}))
        for name, values in {'positions': xyz @ transform[:3, :3].T,
                             'refined_class': np.array([1, 2, 3, 3], np.uint8),
                             'internal_type': np.array([0, 0, 1, 5], np.uint8),
                             'internal_instance': np.array([0, 0, 7, 0], np.uint32)}.items():
            np.save(run / (name + '.npy'), values)
        manifest = {'completed': True, 'priorMode': 'off', 'runId': 'test', 'algorithmVersion': 'test',
                    'source': {'pointCount': 4}, 'internalRebar': {'instanceCount': 1,
                    'diagnostics': {'scopeMode': 'all-steel', 'scope': 'all steel'}},
                    'preprocessing': {'tableRemoval': {'plane': {'origin': [0, 0, 0], 'slopes': [0, 0], 'clearanceM': .005}}}}
        (run / 'manifest.json').write_text(json.dumps(manifest))
        return xyz, transform, tiles, run

    def test_one_tileset_preserves_all_points_and_source_coordinates(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            xyz, transform, tiles, run = self.inputs(root)
            original = (tiles / 'content.pnts').read_bytes()
            result = publish_preview(root / 'source.las', tiles, run, root / 'out', transform)
            self.assertEqual(result['sourcePointCount'], 4)
            self.assertEqual(result['tilesPointCount'], 4)
            self.assertEqual(result['assignedPointCount'], 1)
            self.assertEqual(result['classCounts'], {'0': 0, '1': 1, '2': 1, '3': 1, '4': 1})
            np.testing.assert_allclose(result['plane']['normal'], [0, 0, -1])
            np.testing.assert_allclose(pnts_positions(root / 'out/tiles/content.pnts'), xyz)
            _, _, batch, binary = _read(root / 'out/tiles/content.pnts')
            np.testing.assert_array_equal(np.frombuffer(binary, 'u1', count=4, offset=batch['REBAR_CLASS']['byteOffset']), [1, 2, 3, 4])
            np.testing.assert_array_equal(np.frombuffer(binary, '<u4', count=4, offset=batch['REBAR_INSTANCE']['byteOffset']), [0, 0, 7, 0])
            self.assertEqual((tiles / 'content.pnts').read_bytes(), original)
            self.assertEqual(len(list((root / 'out').rglob('tileset.json'))), 1)

    def test_wrong_frame_or_record_order_cannot_publish_mislabelled_tiles(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _, transform, tiles, run = self.inputs(root)
            with self.assertRaisesRegex(ValueError, 'record order'):
                publish_preview(root / 'source.las', tiles, run, root / 'out')
            self.assertFalse((root / 'out').exists())
            source_positions = np.load(run / 'positions.npy')
            np.save(run / 'positions.npy', source_positions[::-1])
            with self.assertRaisesRegex(ValueError, 'record order'):
                publish_preview(root / 'source.las', tiles, run, root / 'out', transform)
            self.assertFalse((root / 'out').exists())

if __name__ == '__main__':
    unittest.main()
