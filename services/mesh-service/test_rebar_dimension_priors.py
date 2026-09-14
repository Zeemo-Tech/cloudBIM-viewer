from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

import ifcopenshell
import ifcopenshell.api

from algorithms import rebar_dimension_priors as priors


ROOT = Path(__file__).resolve().parents[2]
CURRENT_SOURCE = ROOT / "backend/data/assets/95b6b41c5857d9eb3407b155/source.las"
CURRENT_IFC = ROOT / "backend/data/uploads/419278dd32c37508da3af8bd/source"


def _model(path: Path) -> None:
    model = ifcopenshell.file(schema="IFC4")
    ifcopenshell.api.run("root.create_entity", model, ifc_class="IfcProject", name="Dimension fixture")
    unit = ifcopenshell.api.run("unit.add_si_unit", model, unit_type="LENGTHUNIT", prefix="MILLI")
    ifcopenshell.api.run("unit.assign_unit", model, units=[unit])
    context = ifcopenshell.api.run("context.add_context", model, context_type="Model")
    body = ifcopenshell.api.run(
        "context.add_context", model, context_type="Model", context_identifier="Body",
        target_view="MODEL_VIEW", parent=context,
    )
    point = lambda values: model.create_entity("IfcCartesianPoint", Coordinates=tuple(float(v) for v in values))
    direction = lambda values: model.create_entity("IfcDirection", DirectionRatios=tuple(float(v) for v in values))
    axis = model.create_entity("IfcAxis2Placement3D", Location=point((0, 0, 0)))

    def product(name, item):
        element = ifcopenshell.api.run("root.create_entity", model, ifc_class="IfcBuildingElementProxy", name=name)
        element.ObjectPlacement = model.create_entity("IfcLocalPlacement", RelativePlacement=axis)
        representation = model.create_entity(
            "IfcShapeRepresentation", ContextOfItems=body, RepresentationIdentifier="Body",
            RepresentationType="SweptSolid", Items=(item,),
        )
        element.Representation = model.create_entity("IfcProductDefinitionShape", Representations=(representation,))

    def straight(name, diameter, depth, extrusion=(1, 0, 0)):
        profile = model.create_entity("IfcCircleProfileDef", ProfileType="AREA", Radius=diameter / 2)
        solid = model.create_entity(
            "IfcExtrudedAreaSolid", SweptArea=profile, Position=axis,
            ExtrudedDirection=direction(extrusion), Depth=depth,
        )
        product(name, solid)

    straight("8 mm horizontal", 8, 1150)
    straight("12 mm horizontal", 12, 280, extrusion=(0, 1, 0))
    straight("8 mm vertical", 8, 500, extrusion=(0, 0, 1))
    directrix = model.create_entity("IfcPolyline", Points=(point((0, 0, 0)), point((1000, 0, 0)), point((1000, 500, 0))))
    product("12 mm bent", model.create_entity("IfcSweptDiskSolid", Directrix=directrix, Radius=6.))
    model.write(str(path))


class RebarDimensionPriorTests(unittest.TestCase):
    def setUp(self):
        priors._CACHE.clear()
        priors._DIGEST_CACHE.clear()

    def test_extracts_native_units_multiple_diameters_and_only_straight_horizontal_lengths(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "dimensions.ifc"
            _model(path)
            result = priors.load_dimension_priors(ifc_path=str(path))
        self.assertTrue(result["available"])
        self.assertEqual(result["provenance"]["unitScale"], .001)
        self.assertEqual(result["diametersM"], [.008, .012])
        self.assertEqual(result["horizontalLengthsM"], [.28, 1.15])
        self.assertEqual(result["counts"]["shapeCount"], 4)
        self.assertEqual(result["counts"]["bentCount"], 1)
        bent = next(family for family in result["families"] if family["shape"] == "bent")
        self.assertEqual(bent["lengthM"], 1.5)
        self.assertNotIn(bent["lengthM"], result["horizontalLengthsM"])
        self.assertEqual(bent["lengthSemantics"], "centerlinePath")
        json.dumps(result)

    def test_invalid_input_returns_json_safe_unavailable_report(self):
        result = priors.load_dimension_priors(ifc_path="/definitely/missing/model.ifc")
        self.assertFalse(result["available"])
        self.assertEqual(result["reason"], "ifc_path_unavailable")
        json.dumps(result)

    def test_unassociated_source_does_not_pick_an_arbitrary_neighbor_ifc(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "assets" / "unrelated" / "source.las"
            source.parent.mkdir(parents=True)
            source.write_bytes(b"not the registered scan")
            _model(root / "candidate.ifc")
            result = priors.load_dimension_priors(source_path=str(source))
        self.assertFalse(result["available"])
        self.assertEqual(result["reason"], "no_unambiguous_ifc_association")

    def test_registered_asset_id_still_requires_registered_source_hash(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "assets" / "95b6b41c5857d9eb3407b155" / "source.las"
            source.parent.mkdir(parents=True)
            source.write_bytes(b"different scan content")
            result = priors.load_dimension_priors(source_path=str(source))
        self.assertFalse(result["available"])
        self.assertEqual(result["reason"], "no_unambiguous_ifc_association")

    def test_cache_is_keyed_by_resolved_path_and_stat_and_reports_reuse(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "dimensions.ifc"
            _model(path)
            first = priors.load_dimension_priors(ifc_path=str(path))
            second = priors.load_dimension_priors(ifc_path=str(path))
        self.assertFalse(first["cache"]["reused"])
        self.assertTrue(second["cache"]["reused"])
        self.assertEqual(first["families"], second["families"])

    @unittest.skipUnless(CURRENT_SOURCE.is_file() and CURRENT_IFC.is_file(), "local YB-1 assets unavailable")
    def test_source_only_never_uses_a_repository_hardcoded_ifc_association(self):
        result = priors.load_dimension_priors(source_path=str(CURRENT_SOURCE))
        self.assertFalse(result["available"])
        self.assertEqual(result["reason"], "no_unambiguous_ifc_association")


if __name__ == "__main__":
    unittest.main()
