import sys
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
FEISHU_OPS = PROJECT_ROOT / "feishu_ops"
if str(FEISHU_OPS) not in sys.path:
    sys.path.insert(0, str(FEISHU_OPS))


class ResourcePriorityTests(unittest.TestCase):
    def tearDown(self):
        sys.modules.pop("resource_priority", None)
        sys.modules.pop("resource_pool", None)

    def test_scores_p1_customer_delivery(self):
        from resource_priority import score_resource_request

        score = score_resource_request(
            urgency="P1",
            deadline="明天下午6点",
            reason="客户验收前需要跑精度测试",
            pool_can_satisfy=True,
            pool_is_tight=False,
            accept_queue=True,
            accept_downgrade=False,
        )

        self.assertGreaterEqual(score.score, 110)
        self.assertIn("P1: +70", score.reasons)
        self.assertIn("客户交付/验收: +30", score.reasons)
        self.assertIn("资源池可满足: +10", score.reasons)

    def test_scores_pool_tight_penalty(self):
        from resource_priority import score_resource_request

        score = score_resource_request(
            urgency="P2",
            deadline="",
            reason="内部测试",
            pool_can_satisfy=False,
            pool_is_tight=True,
            accept_queue=False,
            accept_downgrade=False,
        )

        self.assertEqual(score.score, 30)
        self.assertIn("P2: +40", score.reasons)
        self.assertIn("内部测试: +10", score.reasons)
        self.assertIn("资源池紧张: -20", score.reasons)


if __name__ == "__main__":
    unittest.main()
