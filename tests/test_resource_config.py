import sys
import tempfile
import textwrap
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
FEISHU_OPS = PROJECT_ROOT / "feishu_ops"
if str(FEISHU_OPS) not in sys.path:
    sys.path.insert(0, str(FEISHU_OPS))


class ResourceConfigTests(unittest.TestCase):
    def tearDown(self):
        sys.modules.pop("resource_config", None)

    def test_load_resource_pools_builds_node_whitelist_and_sshuser_path(self):
        from resource_config import load_resource_pools

        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "resource_pools.yml"
            path.write_text(textwrap.dedent("""
                resource_pools:
                  - pool_id: k100_train
                    name: K100-训练池
                    description: 通用训练池
                    resource_type: K100
                    nodes: [node01, node02]
                    sshuser_path: /public/bin/sshuser
                    total_devices: 16
                    default_grant_hours: 72
                    max_grant_hours: 168
                    min_free_devices_for_auto_suggest: 4
                    enabled: true
                    prometheus:
                      labels:
                        pool: k100_train
                        accelerator: k100
            """), encoding="utf-8")

            config = load_resource_pools(str(path))

        self.assertEqual(len(config.pools), 1)
        self.assertEqual(config.pools[0].pool_id, "k100_train")
        self.assertEqual(config.pools[0].sshuser_path, "/public/bin/sshuser")
        self.assertIn("node01", config.allowed_nodes)
        self.assertIn("node02", config.allowed_nodes)

    def test_duplicate_pool_id_is_rejected(self):
        from resource_config import ResourceConfigError, load_resource_pools

        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "resource_pools.yml"
            path.write_text(textwrap.dedent("""
                resource_pools:
                  - pool_id: k100_train
                    name: A
                    resource_type: K100
                    nodes: [node01]
                    total_devices: 8
                    default_grant_hours: 24
                    max_grant_hours: 72
                    enabled: true
                  - pool_id: k100_train
                    name: B
                    resource_type: K100
                    nodes: [node02]
                    total_devices: 8
                    default_grant_hours: 24
                    max_grant_hours: 72
                    enabled: true
            """), encoding="utf-8")

            with self.assertRaises(ResourceConfigError):
                load_resource_pools(str(path))

    def test_default_hours_cannot_exceed_max_hours(self):
        from resource_config import ResourceConfigError, load_resource_pools

        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "resource_pools.yml"
            path.write_text(textwrap.dedent("""
                resource_pools:
                  - pool_id: bad_pool
                    name: Bad
                    resource_type: K100
                    nodes: [node01]
                    total_devices: 8
                    default_grant_hours: 100
                    max_grant_hours: 72
                    enabled: true
            """), encoding="utf-8")

            with self.assertRaises(ResourceConfigError):
                load_resource_pools(str(path))


if __name__ == "__main__":
    unittest.main()
