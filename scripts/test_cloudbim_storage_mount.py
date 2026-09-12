"""Verify backend and mesh-service see the same files in both storage modes."""

import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]


class SharedStorageTests(unittest.TestCase):
    def check_mounts(self, override=None):
        env = {key: value for key, value in os.environ.items()
               if not key.startswith("CLOUDBIM_")}
        env.update(DB_PASSWORD="storage-test", JWT_SECRET="storage-test",
                   CLOUDBIM_GOCESIUMTILER_DIR="/tmp/storage-test-tiler",
                   CLOUDBIM_IFC_BUNDLE_DIR="/tmp/storage-test-ifc")
        if override is not None:
            env["CLOUDBIM_DATA_MOUNT"] = override
        result = subprocess.run(
            ["docker", "compose", "--env-file", "/dev/null", "-f",
             str(ROOT / "docker-compose.yml"), "config", "--format", "json"],
            env=env, check=True, capture_output=True, text=True,
        )
        services = json.loads(result.stdout)["services"]
        backend = next(v for v in services["backend"]["volumes"]
                       if v["target"] == "/app/data")
        mesh = next(v for v in services["mesh-service"]["volumes"]
                    if v["target"] == "/storage")
        self.assertEqual((backend["type"], backend["source"]),
                         (mesh["type"], mesh["source"]))
        self.assertEqual(backend["type"], "bind" if override else "volume")
        self.assertFalse(backend.get("read_only", False))
        self.assertFalse(mesh.get("read_only", False))

    def test_default_named_volume(self):
        self.check_mounts()

    def test_local_data_directory(self):
        with tempfile.TemporaryDirectory(prefix="cloudbim-storage-") as directory:
            self.check_mounts(directory)


if __name__ == "__main__":
    unittest.main()
