import sys
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
FEISHU_OPS = PROJECT_ROOT / "feishu_ops"
if str(FEISHU_OPS) not in sys.path:
    sys.path.insert(0, str(FEISHU_OPS))


class FakeResponse:
    def __init__(self, payload):
        self.payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self.payload


class ResourcePrometheusTests(unittest.TestCase):
    def tearDown(self):
        sys.modules.pop("resource_prometheus", None)
        sys.modules.pop("resource_config", None)

    def test_empty_url_returns_unknown_status(self):
        from resource_config import ResourcePool
        from resource_prometheus import PrometheusResourceClient

        pool = ResourcePool(
            pool_id="k100_train",
            name="K100",
            resource_type="K100",
            nodes=["node01"],
            total_devices=8,
            default_grant_hours=24,
            max_grant_hours=72,
        )
        status = PrometheusResourceClient("").get_pool_status(pool)

        self.assertEqual(status.pool_id, "k100_train")
        self.assertEqual(status.state, "unknown")
        self.assertIsNone(status.free_devices)

    def test_query_pool_status_parses_prometheus_vector_value(self):
        from resource_config import ResourcePool
        from resource_prometheus import PrometheusResourceClient

        calls = []

        def fake_get(url, params, timeout):
            calls.append((url, params, timeout))
            return FakeResponse({
                "status": "success",
                "data": {
                    "result": [
                        {"value": [1710000000, "5"]}
                    ]
                }
            })

        pool = ResourcePool(
            pool_id="k100_train",
            name="K100",
            resource_type="K100",
            nodes=["node01"],
            total_devices=8,
            default_grant_hours=24,
            max_grant_hours=72,
            prometheus_labels={"pool": "k100_train"},
        )
        client = PrometheusResourceClient("http://prometheus:9090", http_get=fake_get, timeout_seconds=3)
        status = client.get_pool_status(pool)

        self.assertEqual(status.state, "ok")
        self.assertEqual(status.free_devices, 5)
        self.assertEqual(status.total_devices, 8)
        self.assertEqual(calls[0][0], "http://prometheus:9090/api/v1/query")
        self.assertEqual(calls[0][2], 3)

    def test_query_errors_fall_back_to_unknown(self):
        from resource_config import ResourcePool
        from resource_prometheus import PrometheusResourceClient

        def fake_get(url, params, timeout):
            raise RuntimeError("network down")

        pool = ResourcePool(
            pool_id="k100_train",
            name="K100",
            resource_type="K100",
            nodes=["node01"],
            total_devices=8,
            default_grant_hours=24,
            max_grant_hours=72,
        )
        status = PrometheusResourceClient("http://prometheus:9090", http_get=fake_get).get_pool_status(pool)

        self.assertEqual(status.state, "unknown")
        self.assertIn("network down", status.error)


if __name__ == "__main__":
    unittest.main()
