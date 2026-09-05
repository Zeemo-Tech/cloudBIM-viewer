"""Service-contract, loader, and CLI tests for the geometric rebar PoC."""

from __future__ import annotations

import asyncio
import json
import tempfile
import unittest
from dataclasses import asdict
from functools import wraps
from pathlib import Path
from unittest import mock

import laspy
import numpy as np
from fastapi import FastAPI
from fastapi.responses import JSONResponse

import rebar_poc
import main as mesh_main
from algorithms.rebar_segmentation import (
    InvalidPointCloudError,
    RebarSegmentationParams,
)
from rebar_api import RebarParams, create_rebar_router
from rebar_poc import (
    LoadedPointCloud,
    PointCloudInputError,
    load_point_cloud,
    run_segmentation_file,
    write_json_atomically,
)


def _write_ascii_ply(path: Path, points: np.ndarray) -> None:
    with path.open("w", encoding="ascii") as output:
        output.write("ply\n")
        output.write("format ascii 1.0\n")
        output.write(f"element vertex {len(points)}\n")
        output.write("property float x\n")
        output.write("property float y\n")
        output.write("property float z\n")
        output.write("end_header\n")
        for point in points:
            output.write(f"{point[0]} {point[1]} {point[2]}\n")


class _AsgiResponse:
    def __init__(self, status_code: int, headers: dict[str, str], body: bytes):
        self.status_code = status_code
        self.headers = headers
        self.content = body
        self.text = body.decode("utf-8")

    def json(self):
        return json.loads(self.content)


async def _asgi_post(app: FastAPI, path: str, payload: dict) -> _AsgiResponse:
    """Drive FastAPI directly because the pinned environment omits httpx."""

    body = json.dumps(payload).encode("utf-8")
    request_sent = False
    messages: list[dict] = []

    async def receive():
        nonlocal request_sent
        if request_sent:
            return {"type": "http.disconnect"}
        request_sent = True
        return {"type": "http.request", "body": body, "more_body": False}

    async def send(message):
        messages.append(message)

    scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": "POST",
        "scheme": "http",
        "path": path,
        "raw_path": path.encode("ascii"),
        "query_string": b"",
        "root_path": "",
        "headers": [
            (b"content-type", b"application/json"),
            (b"content-length", str(len(body)).encode("ascii")),
        ],
        "client": ("test", 1234),
        "server": ("testserver", 80),
    }
    await app(scope, receive, send)
    start = next(message for message in messages if message["type"] == "http.response.start")
    response_body = b"".join(
        message.get("body", b"")
        for message in messages
        if message["type"] == "http.response.body"
    )
    headers = {
        key.decode("latin-1"): value.decode("latin-1")
        for key, value in start["headers"]
    }
    return _AsgiResponse(start["status"], headers, response_body)


class PointCloudLoaderTests(unittest.TestCase):
    def test_loads_small_ply_and_retains_first_point_per_voxel(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = root / "small.ply"
            points = np.array(
                [
                    [10.000, 20.000, 30.000],
                    [10.004, 20.000, 30.000],
                    [10.025, 20.000, 30.000],
                    [10.026, 20.000, 30.000],
                ]
            )
            _write_ascii_ply(path, points)

            loaded = load_point_cloud(
                path,
                max_input_points=10,
                voxel_size=0.01,
                storage_root=root,
            )

            np.testing.assert_allclose(loaded.points, points[[0, 2]], atol=1e-6)
            np.testing.assert_array_equal(loaded.source_indices, [0, 2])
            self.assertEqual(loaded.report["sampling"]["method"], "voxel_first_point")
            self.assertEqual(
                loaded.report["sampling"]["representative"],
                "first finite point in source-reader order per voxel",
            )
            self.assertEqual(
                loaded.report["point_index_contract"]["detection_to_source_index"],
                [0, 2],
            )

    def test_loads_small_las_with_scaled_coordinates(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = root / "small.las"
            header = laspy.LasHeader(point_format=3, version="1.2")
            header.scales = np.array([0.001, 0.001, 0.001])
            cloud = laspy.LasData(header)
            cloud.x = np.array([1.001, 1.020, 1.040])
            cloud.y = np.array([2.002, 2.020, 2.040])
            cloud.z = np.array([0.003, 0.020, 0.040])
            cloud.write(path)

            loaded = load_point_cloud(
                path,
                max_input_points=3,
                storage_root=root,
            )

            self.assertEqual(loaded.report["format"], "las")
            self.assertEqual(loaded.report["raw_point_count"], 3)
            self.assertEqual(loaded.report["sampling"]["method"], "none")
            self.assertEqual(
                loaded.report["sampling"]["representative"],
                "all finite points in source-reader order",
            )
            np.testing.assert_allclose(
                loaded.points,
                [[1.001, 2.002, 0.003], [1.020, 2.020, 0.020], [1.040, 2.040, 0.040]],
                atol=1e-9,
            )

    def test_las_voxel_sampling_deduplicates_across_chunks(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = root / "chunked.las"
            header = laspy.LasHeader(point_format=3, version="1.2")
            header.scales = np.array([0.001, 0.001, 0.001])
            cloud = laspy.LasData(header)
            cloud.x = np.array([0.000, 0.004, 0.006, 0.011, 0.019, 0.021])
            cloud.y = np.zeros(6)
            cloud.z = np.zeros(6)
            cloud.write(path)

            # Chunk size 2 makes the first voxel span two chunks.  Header-min
            # anchoring and cross-chunk state must still select records 0,3,5.
            with mock.patch("rebar_poc.LAS_CHUNK_SIZE", 2):
                loaded = load_point_cloud(
                    path,
                    max_input_points=10,
                    voxel_size=0.01,
                    storage_root=root,
                )

            np.testing.assert_array_equal(loaded.source_indices, [0, 3, 5])
            self.assertEqual(loaded.report["reader"], "laspy.chunk_iterator")
            self.assertEqual(loaded.report["sampling"]["method"], "voxel_first_point")
            self.assertFalse(
                loaded.report["sampling"]["voxel_selection_truncated"]
            )

    def test_las_automatic_cap_is_chunk_size_independent_stable_stride(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = root / "stride.las"
            header = laspy.LasHeader(point_format=3, version="1.2")
            header.scales = np.array([0.001, 0.001, 0.001])
            cloud = laspy.LasData(header)
            cloud.x = np.arange(10) * 0.01
            cloud.y = np.zeros(10)
            cloud.z = np.zeros(10)
            cloud.write(path)

            with mock.patch("rebar_poc.LAS_CHUNK_SIZE", 3):
                first = load_point_cloud(path, max_input_points=3, storage_root=root)
            with mock.patch("rebar_poc.LAS_CHUNK_SIZE", 7):
                second = load_point_cloud(path, max_input_points=3, storage_root=root)

            np.testing.assert_array_equal(first.source_indices, [0, 4, 8])
            np.testing.assert_array_equal(first.source_indices, second.source_indices)
            np.testing.assert_allclose(first.points, second.points)
            self.assertEqual(first.report["sampling"]["method"], "stable_stride")
            self.assertEqual(first.report["sampling"]["stride"], 4)
            self.assertEqual(
                first.report["sampling"]["representative"],
                "every stride-th finite point in source-reader order",
            )

    def test_filters_non_finite_points_before_stable_sampling(self):
        source = np.array(
            [
                [0.0, 0.0, 0.0],
                [np.nan, 0.0, 0.0],
                [1.0, 0.0, 0.0],
                [2.0, np.inf, 0.0],
                [3.0, 0.0, 0.0],
            ]
        )
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "mock.pcd"
            path.touch()
            with mock.patch("rebar_poc._read_source_points", return_value=source):
                loaded = load_point_cloud(path, max_input_points=2)

        self.assertEqual(loaded.report["dropped_non_finite_point_count"], 2)
        self.assertLessEqual(len(loaded.points), 2)
        self.assertTrue(np.isfinite(loaded.points).all())
        self.assertTrue(set(loaded.source_indices.tolist()).issubset({0, 2, 4}))
        self.assertTrue(
            loaded.report["sampling"]["method"].startswith("automatic_voxel")
        )

    def test_rejects_symlink_escape_from_shared_storage(self):
        with (
            tempfile.TemporaryDirectory() as storage_directory,
            tempfile.TemporaryDirectory() as outside_directory,
        ):
            storage_root = Path(storage_directory)
            outside = Path(outside_directory) / "outside.ply"
            _write_ascii_ply(outside, np.zeros((1, 3)))
            link = storage_root / "escape.ply"
            link.symlink_to(outside)

            with self.assertRaisesRegex(PointCloudInputError, "inside shared storage"):
                load_point_cloud(link, max_input_points=10, storage_root=storage_root)


class AtomicJsonAndCliTests(unittest.TestCase):
    def test_atomic_json_writer_replaces_existing_file(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "result.json"
            output.write_text("old", encoding="utf-8")

            resolved = write_json_atomically(output, {"ok": True, "label": "钢筋"})

            self.assertEqual(resolved, output.resolve())
            self.assertEqual(
                json.loads(output.read_text(encoding="utf-8")),
                {"ok": True, "label": "钢筋"},
            )
            self.assertEqual(list(Path(directory).glob(".result.json.*.tmp")), [])

    def test_cli_uses_shared_runner_and_writes_json(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source.ply"
            source.touch()
            output = root / "result.json"
            expected = {"schema_version": "test", "instances": []}

            with mock.patch("rebar_poc.run_segmentation_file", return_value=expected) as run:
                exit_code = rebar_poc.main(
                    [
                        str(source),
                        "--params-json",
                        '{"random_seed": 9}',
                        "--max-input-points",
                        "1234",
                        "--output-json",
                        str(output),
                    ]
                )

            self.assertEqual(exit_code, 0)
            self.assertEqual(json.loads(output.read_text(encoding="utf-8")), expected)
            self.assertEqual(run.call_args.kwargs["max_input_points"], 1234)
            self.assertEqual(run.call_args.kwargs["params"].random_seed, 9)

    def test_cli_refuses_to_overwrite_the_input_cloud(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "source.ply"
            source.write_bytes(b"original point cloud")

            with mock.patch("rebar_poc.run_segmentation_file") as run:
                exit_code = rebar_poc.main(
                    [str(source), "--output-json", str(source)]
                )

            self.assertEqual(exit_code, 2)
            self.assertEqual(source.read_bytes(), b"original point cloud")
            run.assert_not_called()

    def test_file_runner_uses_stricter_algorithm_point_cap(self):
        loaded = LoadedPointCloud(
            points=np.zeros((3, 3)),
            source_indices=np.arange(3),
            report={"detection_point_count": 3},
        )
        algorithm_result = {"schema_version": "test", "diagnostics": {}}
        with mock.patch("rebar_poc.load_point_cloud", return_value=loaded) as loader, mock.patch(
            "rebar_poc.segment_rebar_points", return_value=algorithm_result
        ):
            result = run_segmentation_file(
                "/unused/source.las",
                params={"max_point_count": 220},
                max_input_points=400,
            )

        self.assertEqual(loader.call_args.kwargs["max_input_points"], 220)
        self.assertEqual(result["input"]["requested_max_input_points"], 400)
        self.assertEqual(result["input"]["algorithm_max_point_count"], 220)


class RebarApiTests(unittest.TestCase):
    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.storage_root = Path(self.temporary_directory.name)
        self.source = self.storage_root / "source.ply"
        self.source.touch()

    def tearDown(self):
        self.temporary_directory.cleanup()

    def _app(self, *, heavy_task=None) -> FastAPI:
        app = FastAPI()
        app.include_router(
            create_rebar_router(
                heavy_task=heavy_task,
                storage_root=self.storage_root,
            )
        )
        return app

    def _post(self, payload: dict, *, heavy_task=None) -> _AsgiResponse:
        return asyncio.run(
            _asgi_post(self._app(heavy_task=heavy_task), "/rebar/segment", payload)
        )

    def test_main_app_registers_the_rebar_contract(self):
        schema = mesh_main.app.openapi()

        self.assertIn("/rebar/segment", schema["paths"])
        request_schema = schema["paths"]["/rebar/segment"]["post"]["requestBody"]
        self.assertIn("application/json", request_schema["content"])

    def test_api_parameter_defaults_match_geometry_core(self):
        self.assertEqual(RebarParams().model_dump(), asdict(RebarSegmentationParams()))

    @mock.patch("rebar_api.run_segmentation_file")
    def test_success_forwards_validated_request(self, run):
        run.return_value = {
            "schema_version": "rebar-geometric-poc-v2",
            "instances": [],
        }

        response = self._post(
            {
                "point_cloud_path": str(self.source),
                "max_input_points": 1234,
                "voxel_size": 0.004,
                "params": {"random_seed": 17, "max_point_count": 2000},
            },
        )

        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["schema_version"], "rebar-geometric-poc-v2")
        self.assertEqual(run.call_args.kwargs["max_input_points"], 1234)
        self.assertEqual(run.call_args.kwargs["voxel_size"], 0.004)
        self.assertEqual(run.call_args.kwargs["params"]["random_seed"], 17)
        self.assertEqual(run.call_args.kwargs["storage_root"], str(self.storage_root))

    def test_pydantic_and_cross_field_errors_are_422(self):
        response = self._post(
            {
                "point_cloud_path": str(self.source),
                "unexpected": True,
                "params": {
                    "axis_distance_threshold": 0.02,
                    "offset_cluster_gap": 0.01,
                },
            },
        )

        self.assertEqual(response.status_code, 422)

    def test_excessive_single_request_work_is_422(self):
        response = self._post(
            {
                "point_cloud_path": str(self.source),
                "max_input_points": 200_000,
                "params": {"plane_ransac_iterations": 501},
            },
        )

        self.assertEqual(response.status_code, 422)
        self.assertIn("service work budget", response.text)

    def test_missing_and_unsupported_files_are_400(self):
        missing = self._post(
            {"point_cloud_path": str(self.storage_root / "missing.ply")},
        )
        unsupported_path = self.storage_root / "source.xyz"
        unsupported_path.touch()
        unsupported = self._post(
            {"point_cloud_path": str(unsupported_path)},
        )

        self.assertEqual(missing.status_code, 400)
        self.assertEqual(unsupported.status_code, 400)
        self.assertIn("supported", unsupported.json()["msg"])

    def test_path_outside_shared_storage_is_403(self):
        with tempfile.TemporaryDirectory() as outside_directory:
            outside = Path(outside_directory) / "source.ply"
            outside.touch()
            response = self._post(
                {"point_cloud_path": str(outside)},
            )

        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.json()["code"], 403)

    @mock.patch("rebar_api.run_segmentation_file")
    def test_geometry_error_is_422(self, run):
        run.side_effect = InvalidPointCloudError("too few distinct samples")

        response = self._post(
            {"point_cloud_path": str(self.source)},
        )

        self.assertEqual(response.status_code, 422)
        self.assertEqual(response.json()["code"], 422)
        self.assertIn("too few", response.json()["msg"])

    @mock.patch("rebar_api.run_segmentation_file")
    def test_unexpected_error_is_sanitized_500(self, run):
        run.side_effect = RuntimeError("sensitive internals")

        response = self._post(
            {"point_cloud_path": str(self.source)},
        )

        self.assertEqual(response.status_code, 500)
        self.assertNotIn("sensitive", response.text)

    def test_injected_heavy_task_gate_can_return_429(self):
        def busy_gate(task_name):
            self.assertEqual(task_name, "rebar-segment")

            def decorate(func):
                @wraps(func)
                def wrapped(*args, **kwargs):
                    return JSONResponse(
                        status_code=429,
                        content={"code": 429, "msg": "busy", "activeTask": "c2m"},
                        headers={"Retry-After": "5"},
                    )

                return wrapped

            return decorate

        response = self._post(
            {"point_cloud_path": str(self.source)},
            heavy_task=busy_gate,
        )

        self.assertEqual(response.status_code, 429)
        self.assertEqual(response.headers["retry-after"], "5")
        self.assertEqual(response.json()["activeTask"], "c2m")


if __name__ == "__main__":
    unittest.main()
