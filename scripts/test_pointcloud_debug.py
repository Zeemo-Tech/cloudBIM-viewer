import importlib.util
from pathlib import Path
import unittest


SCRIPT = Path(__file__).with_name("pointcloud-debug.py")
SPEC = importlib.util.spec_from_file_location("pointcloud_debug", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class LoopbackHostHeaderTest(unittest.TestCase):
    def test_accepts_forwarded_loopback_ports(self):
        for value in ("localhost:8766", "localhost:56446", "127.0.0.1:56446", "[::1]:56446"):
            with self.subTest(value=value):
                self.assertTrue(MODULE.is_loopback_host_header(value))

    def test_rejects_non_loopback_or_malformed_hosts(self):
        for value in ("", "example.com:8766", "localhost.example:8766", "localhost@evil.test", "localhost:99999"):
            with self.subTest(value=value):
                self.assertFalse(MODULE.is_loopback_host_header(value))


if __name__ == "__main__":
    unittest.main()
