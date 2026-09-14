"""Cropped morphology must preserve the full-raster predicate at source pixels."""
import unittest

import numpy as np
from scipy import ndimage

from algorithms.projection_geometry_classifier import (
    ProjectionParameters, _disk, _long_thin_components, _side_slice_candidates,
)


def full_raster_reference(ids, shape, pixel, params, closing, wide, dilation):
    present = np.zeros(shape, bool)
    present.ravel()[ids] = True
    joined = ndimage.binary_closing(present, structure=closing)
    opened = ndimage.binary_opening(joined, structure=wide)
    static = ndimage.binary_dilation(opened, structure=dilation)
    thin = _long_thin_components(joined & ~static, pixel, params)
    thin = ndimage.binary_dilation(thin, structure=dilation) & ~static & present
    return thin.ravel()[ids]


class ProjectionSliceEquivalenceTests(unittest.TestCase):
    def test_crop_matches_full_raster_at_real_edges_and_zero_halo(self):
        rng = np.random.default_rng(122)
        shape = (97, 251)
        masks = []
        # Long and diagonal rods, broad fixtures, gaps/crossings, all four
        # original borders, plus sparse and densely filled local windows.
        for y, x in ((0, 0), (0, 195), (61, 0), (61, 195), (30, 90)):
            mask = np.zeros(shape, bool)
            mask[y:y+30, x:x+2] = True
            mask[y+12:y+15, x:x+50] = True
            mask[y+18:y+34, x+20:x+48] = True
            mask[y+13, x+2:x+5] = False
            masks.append(mask)
        for density in (.02, .2, .7, 1.):
            mask = np.zeros(shape, bool)
            mask[25:60, 80:160] = rng.random((35, 80)) < density
            masks.append(mask)
        diagonal = np.zeros(shape, bool)
        diagonal[np.arange(70)+12, np.arange(70)+40] = True
        masks += [diagonal, np.zeros(shape, bool), np.ones(shape, bool)]
        for radius in (1, 3, 11):
            pixel = .002
            params = ProjectionParameters(max_bar_width=2*radius*pixel)
            closing, wide, dilation = np.ones((3, 3), bool), _disk(radius), _disk(1)
            for mask in masks:
                ids = np.flatnonzero(mask)
                # Duplicates and a shuffled input must still return source order.
                ids = np.repeat(ids, 2)
                rng.shuffle(ids)
                with self.subTest(radius=radius, points=len(ids)):
                    expected = full_raster_reference(ids, shape, pixel, params, closing, wide, dilation)
                    actual = _side_slice_candidates(ids, shape, pixel, params, closing, wide, dilation)
                    np.testing.assert_array_equal(actual, expected)


if __name__ == '__main__':
    unittest.main()
