"""Conservative design-inferred web join tests."""
import copy
import unittest
from unittest.mock import patch

import numpy as np
from scipy.spatial import cKDTree

from algorithms.rebar_control_curves import (
    _inferred_join,
    _stage_inferred_attachments,
    fit_curved_pieces,
)
from algorithms.rebar_design_curves import build_design_curve_pieces


def web_rows():
    return [
        {"id": 1, "designUnitId": "a", "designBarId": "bar", "kind": "web",
         "status": "fitted", "reason": "fixed-radius-surface-supported",
         "centerlineM": [[-.1, 0., 0.], [0., 0., 0.]],
         "designLengthM": .1, "pointCount": 17},
        {"id": 2, "designUnitId": "b", "designBarId": "bar", "kind": "web",
         "status": "fitted", "reason": "fixed-radius-surface-supported",
         "centerlineM": [[.02, .02, 0.], [.02, .12, 0.]],
         "designLengthM": .1, "pointCount": 19},
    ]


def analytic_piece(piece_id="bar/curve/join/a--b", right="b"):
    radius=.015
    phi=np.linspace(0.,np.pi/2,25)
    curve=np.column_stack((.02-radius+radius*np.sin(phi),
                           radius*(1.-np.cos(phi)),np.zeros(len(phi))))
    return {
        "id":piece_id,"designBarId":"bar","kind":"join",
        "unitIds":["a",right],"radiusM":.003,"designLengthM":radius*np.pi/2,
        "geometrySource":"ifc-analytic","centerlineM":curve.tolist(),
        "primitives":[{"kind":"arc","startM":curve[0].tolist(),
                       "endM":curve[-1].tolist(),"centerM":[.005,.015,0.],
                       "normal":[0.,0.,1.],"radiusM":radius,"sweepRad":np.pi/2}],
        "anchors":[{"designUnitId":"a","side":"end"},
                   {"designUnitId":right,"side":"start"}],
    }


def inventory(rows):
    units=[]
    for row in rows:
        curve=np.asarray(row["centerlineM"],float)
        units.append({"designUnitId":row["designUnitId"],
                      "designBarId":row["designBarId"],"kind":row["kind"],
                      "startM":curve[0].tolist(),"endM":curve[-1].tolist(),
                      "diameterM":.006})
    return {"units":units,"bars":[]}


def builder_inventory():
    """Small analytic inventory shaped like the real 75-degree web joins."""
    radius=.015
    sweep=1.3140183714680211
    start=np.zeros(3)
    center=np.array([0.,radius,0.])
    end=center+np.array([radius*np.sin(sweep),-radius*np.cos(sweep),0.])
    first=np.array([1.,0.,0.])
    second=np.array([np.cos(sweep),np.sin(sweep),0.])
    units=[
        {"designUnitId":"a","designBarId":"bar","ordinal":0,
         "startM":(start-.1*first).tolist(),"endM":start.tolist(),
         "direction":first.tolist(),"lengthM":.1,"diameterM":.006,"kind":"web"},
        {"designUnitId":"b","designBarId":"bar","ordinal":1,
         "startM":end.tolist(),"endM":(end+.1*second).tolist(),
         "direction":second.tolist(),"lengthM":.1,"diameterM":.006,"kind":"web"},
    ]
    primitives=[
        {"kind":"line","startM":units[0]["startM"],"endM":start.tolist()},
        {"kind":"arc","startM":start.tolist(),"endM":end.tolist(),
         "centerM":center.tolist(),"normal":[0.,0.,1.],
         "radiusM":radius,"sweepRad":sweep},
        {"kind":"line","startM":end.tolist(),"endM":units[1]["endM"]},
    ]
    return {"units":units,"bars":[{"designBarId":"bar","radiusM":.003,
                                    "curvePrimitives":primitives}]}


def fitted_builder_rows(source_inventory):
    units=source_inventory["units"]
    start=np.asarray(units[0]["endM"],float)
    end=np.asarray(units[1]["startM"],float)
    sweep=source_inventory["bars"][0]["curvePrimitives"][1]["sweepRad"]
    rotation=.146
    first=np.array([np.cos(-rotation),np.sin(-rotation),0.])
    second=np.array([np.cos(sweep+rotation),np.sin(sweep+rotation),0.])
    return [
        {"id":1,"designUnitId":"a","designBarId":"bar","kind":"web",
         "status":"fitted","centerlineM":[(start-.1*first).tolist(),start.tolist()],
         "designLengthM":.1,"pointCount":17},
        {"id":2,"designUnitId":"b","designBarId":"bar","kind":"web",
         "status":"fitted","centerlineM":[end.tolist(),(end+.1*second).tolist()],
         "designLengthM":.1,"pointCount":19},
    ]


def run(pieces, rows, owner=None, status=None):
    owner=np.array([0,9,0],np.uint32) if owner is None else owner
    status=np.array([2,1,4],np.uint8) if status is None else status
    points=np.array([[4.,4.,4.],[5.,5.,5.],[6.,6.,6.]])
    before=(owner.copy(),status.copy(),points.copy())
    with patch("algorithms.rebar_design_curves.build_design_curve_pieces",
               return_value=pieces):
        reports,summary=fit_curved_pieces(
            points,None,inventory(rows),rows,status,owner,
            tree=cKDTree(np.empty((0,3))),source_ids=np.empty(0,np.int64))
    return reports,summary,before,(owner,status,points)


def run_built(source_inventory, rows):
    owner=np.array([0,9,0],np.uint32)
    status=np.array([2,1,4],np.uint8)
    points=np.array([[4.,4.,4.],[5.,5.,5.],[6.,6.,6.]])
    reports,summary=fit_curved_pieces(
        points,None,source_inventory,rows,status,owner,
        tree=cKDTree(np.empty((0,3))),source_ids=np.empty(0,np.int64))
    return reports,summary


class InferredWebJoinTests(unittest.TestCase):
    def test_builder_generated_join_allows_accumulated_endpoint_rotations(self):
        source_inventory=builder_inventory()
        pieces=build_design_curve_pieces(source_inventory)
        self.assertEqual(len(pieces),1)
        self.assertEqual([primitive["kind"] for primitive in pieces[0]["primitives"]],["arc"])
        rows=fitted_builder_rows(source_inventory)

        reports,summary=run_built(source_inventory,rows)

        self.assertEqual(reports[0]["connectionStatus"],"design-inferred")
        self.assertEqual(reports[0]["status"],"pending")
        self.assertEqual(reports[0]["pointCount"],0)
        self.assertNotIn("centerlineM",reports[0])
        self.assertEqual(summary["inferredPieces"],1)

        misaligned=fitted_builder_rows(source_inventory)
        misaligned[0]["centerlineM"]=[[0.,.1,0.],[0.,0.,0.]]
        self.assertIsNone(_inferred_join(
            pieces[0],{row["designUnitId"]:row for row in misaligned}))

    def test_missing_scan_join_uses_body_anchors_without_claiming_points(self):
        rows=web_rows()
        reports,summary,before,after=run([analytic_piece()],rows)
        report=reports[0]

        self.assertEqual(report["status"],"pending")
        self.assertEqual(report["reason"],"insufficient-curve-evidence")
        self.assertEqual(report["pointCount"],0)
        self.assertNotIn("centerlineM",report)
        self.assertEqual(report["connectionStatus"],"design-inferred")
        self.assertEqual(report["inferenceMethod"],"body-anchored-design-join")
        inferred=np.asarray(report["inferredCenterlineM"])
        self.assertAlmostEqual(report["inferredLengthM"],
                               np.linalg.norm(np.diff(inferred,axis=0),axis=1).sum())
        np.testing.assert_allclose(rows[0]["bodyDisplayCenterlineM"][-1],inferred[0])
        np.testing.assert_allclose(rows[1]["bodyDisplayCenterlineM"][0],inferred[-1])
        first=(inferred[1]-inferred[0])/np.linalg.norm(inferred[1]-inferred[0])
        last=(inferred[-1]-inferred[-2])/np.linalg.norm(inferred[-1]-inferred[-2])
        self.assertGreater(first@np.array([1.,0.,0.]),.98)
        self.assertGreater(last@np.array([0.,1.,0.]),.98)
        self.assertEqual(summary["inferredPieces"],1)
        self.assertEqual(summary["fittedPieces"],0)
        self.assertEqual(summary["matchedPoints"],0)
        for actual,original in zip(after,before):
            np.testing.assert_array_equal(actual,original)

    def test_absent_ambiguous_non_web_and_cross_parent_bodies_are_rejected(self):
        cases=[]
        missing=web_rows()[:1]
        cases.append(("absent",missing))
        ambiguous=web_rows()+[copy.deepcopy(web_rows()[1])]
        ambiguous[-1]["id"]=3
        cases.append(("ambiguous",ambiguous))
        non_web=web_rows();non_web[1]["kind"]="short"
        cases.append(("non-web",non_web))
        cross_parent=web_rows();cross_parent[1]["designBarId"]="other"
        cases.append(("cross-parent",cross_parent))
        pending=web_rows();pending[1]["status"]="pending"
        cases.append(("unconfirmed",pending))

        for label,rows in cases:
            with self.subTest(label=label):
                reports,summary,_,_=run([analytic_piece()],rows)
                self.assertNotIn("inferredCenterlineM",reports[0])
                self.assertEqual(summary["inferredPieces"],0)
                self.assertFalse(any("bodyDisplayCenterlineM" in row for row in rows))

    def test_invalid_sampled_and_multiple_design_arcs_are_rejected(self):
        cases=[]
        sampled=analytic_piece();sampled["geometrySource"]="sampled-design"
        cases.append(("sampled",sampled))
        invalid=analytic_piece();invalid["primitives"][0]["sweepRad"]=float("nan")
        cases.append(("invalid",invalid))
        multiple=analytic_piece();multiple["primitives"]*=2
        cases.append(("multiple",multiple))
        incompatible=analytic_piece();incompatible["primitives"][0]["sweepRad"]=.35
        cases.append(("incompatible",incompatible))
        disconnected=analytic_piece();disconnected["centerlineM"][0]=[.5,.5,.5]
        cases.append(("disconnected",disconnected))
        terminal=analytic_piece();terminal["kind"]="terminal"
        cases.append(("terminal",terminal))

        for label,piece in cases:
            with self.subTest(label=label):
                rows=web_rows()
                reports,summary,_,_=run([piece],rows)
                self.assertNotIn("inferredCenterlineM",reports[0])
                self.assertEqual(summary["inferredPieces"],0)

    def test_display_conflicts_and_collapsing_clips_are_atomic(self):
        rows={row["designUnitId"]:row for row in web_rows()}
        occupied={"a":{"end":np.array([.004,0.,0.])}}
        attachments=[({"designUnitId":"a","side":"end"},np.array([.005,0.,0.])),
                     ({"designUnitId":"b","side":"start"},np.array([.02,.015,0.]))]
        self.assertIsNone(_stage_inferred_attachments(rows,occupied,attachments))
        self.assertEqual(set(occupied["a"]),{"end"})

        long={"m":{"centerlineM":[[0.,0.,0.],[1.,0.,0.]]},
              "n":{"centerlineM":[[0.,1.,0.],[1.,1.,0.]]}}
        changes={"m":{"start":np.array([.8,0.,0.])}}
        collapse=[({"designUnitId":"m","side":"end"},np.array([.2,0.,0.])),
                  ({"designUnitId":"n","side":"start"},np.array([.1,1.,0.]))]
        self.assertIsNone(_stage_inferred_attachments(long,changes,collapse))
        np.testing.assert_array_equal(changes["m"]["start"],[.8,0.,0.])

    def test_two_pending_joins_cannot_share_one_body_end(self):
        rows=web_rows()
        third=copy.deepcopy(rows[1]);third.update(id=3,designUnitId="c")
        rows.append(third)
        reports,summary,_,_=run(
            [analytic_piece(),analytic_piece("bar/curve/join/a--c","c")],rows)
        self.assertEqual(["inferredCenterlineM" in report for report in reports],[True,False])
        self.assertEqual(summary["inferredPieces"],1)


if __name__=="__main__":
    unittest.main()
