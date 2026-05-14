import sys
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
FEISHU_OPS = PROJECT_ROOT / "feishu_ops"
if str(FEISHU_OPS) not in sys.path:
    sys.path.insert(0, str(FEISHU_OPS))


class ResourceApprovalTests(unittest.TestCase):
    def tearDown(self):
        sys.modules.pop("resource_approval", None)
        sys.modules.pop("resource_config", None)
        sys.modules.pop("resource_request_store", None)

    def test_parse_approve_command(self):
        from resource_approval import parse_resource_owner_command

        command = parse_resource_owner_command("/approve R12 72h")

        self.assertEqual(command.action, "approve")
        self.assertEqual(command.request_code, "R12")
        self.assertEqual(command.duration_hours, 72)

    def test_parse_reject_command(self):
        from resource_approval import parse_resource_owner_command

        command = parse_resource_owner_command("/reject R12 资源不足")

        self.assertEqual(command.action, "reject")
        self.assertEqual(command.request_code, "R12")
        self.assertEqual(command.reason, "资源不足")

    def test_parse_grant_confirm_and_retry_commands(self):
        from resource_approval import parse_resource_owner_command

        confirm = parse_resource_owner_command("/grant G1 confirm")
        retry = parse_resource_owner_command("/grant G1 retry")

        self.assertEqual(confirm.action, "grant")
        self.assertEqual(confirm.grant_code, "G1")
        self.assertTrue(confirm.confirm)
        self.assertEqual(retry.action, "grant")
        self.assertEqual(retry.grant_code, "G1")
        self.assertEqual(retry.operation, "retry")

    def test_parse_revoke_retry_and_mark_done(self):
        from resource_approval import parse_resource_owner_command

        retry = parse_resource_owner_command("/revoke G1 retry")
        mark_done = parse_resource_owner_command("/revoke G1 mark-done node01,node02")

        self.assertEqual(retry.action, "revoke")
        self.assertEqual(retry.grant_code, "G1")
        self.assertEqual(retry.operation, "retry")
        self.assertEqual(mark_done.action, "revoke")
        self.assertEqual(mark_done.operation, "mark-done")
        self.assertEqual(mark_done.nodes, ["node01", "node02"])

    def test_format_owner_notification_contains_decision_commands(self):
        from resource_approval import format_owner_request_notification
        from resource_request_store import ResourceRequestRecord

        request = ResourceRequestRecord(
            request_code="R1",
            feishu_user_id="ou_user",
            linux_username="zhangsan",
            project_name="客户验收",
            resource_type="K100",
            resource_amount=4,
            duration_hours=72,
            urgency="P1",
            deadline="明天下午6点",
            reason="精度测试",
            accept_queue=True,
            accept_downgrade=False,
            matched_pool_id="k100_train",
            priority_score=115,
            priority_reasons=["P1: +70", "客户交付/验收: +30"],
            status="pending",
        )

        message = format_owner_request_notification(request, pool_name="K100-训练池", free_devices=8)

        self.assertIn("#R1", message)
        self.assertIn("zhangsan", message)
        self.assertIn("优先级评分：115", message)
        self.assertIn("/approve R1 72h", message)
        self.assertIn("/reject R1", message)

    def test_format_phase1_grant_advice_uses_node_local_sshuser_commands(self):
        from resource_approval import format_phase1_grant_advice

        advice = format_phase1_grant_advice(
            request_code="R1",
            linux_username="zhangsan",
            pool_id="k100_train",
            target_nodes=["node01", "node02"],
            sshuser_path="/public/bin/sshuser",
            duration_hours=72,
        )

        self.assertIn("Phase 1", advice)
        self.assertIn("不会自动执行节点命令", advice)
        self.assertIn("node01: /public/bin/sshuser add zhangsan", advice)
        self.assertIn("node02: /public/bin/sshuser add zhangsan", advice)
        self.assertIn("node01: /public/bin/sshuser del zhangsan", advice)
        self.assertNotIn("LD" + "AP", advice)
        self.assertNotIn("user" + "mod", advice)
        self.assertIn("72 小时", advice)


if __name__ == "__main__":
    unittest.main()
