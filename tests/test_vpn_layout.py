import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class VpnLayoutTests(unittest.TestCase):
    def test_qbittorrent_shares_gluetun_network_without_host_torrent_ports(self):
        compose = (ROOT / "compose.yaml").read_text(encoding="utf-8")
        self.assertRegex(compose, r"(?m)^\s+network_mode: service:gluetun$")
        self.assertNotRegex(compose, r"(?m)^\s+- [\"']?6881:6881")


if __name__ == "__main__":
    unittest.main()
