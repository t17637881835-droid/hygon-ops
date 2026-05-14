import sys
import tempfile
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
FEISHU_OPS = PROJECT_ROOT / "feishu_ops"
if str(FEISHU_OPS) not in sys.path:
    sys.path.insert(0, str(FEISHU_OPS))


class ResourceRequestStoreTests(unittest.TestCase):
    def tearDown(self):
        sys.modules.pop("resource_request_store", None)

    def test_create_request_assigns_request_code(self):
        from resource_request_store import ResourceRequestStore

        with tempfile.TemporaryDirectory() as tmpdir:
            store = ResourceRequestStore(f"{tmpdir}/resource.db")
            request = store.create_request(
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
                priority_reasons=["P1: +70"],
            )

            loaded = store.get_request(request.request_code)

        self.assertEqual(request.request_code, "R1")
        self.assertEqual(loaded.linux_username, "zhangsan")
        self.assertEqual(loaded.status, "pending")

    def test_approve_request_changes_status(self):
        from resource_request_store import ResourceRequestStore

        with tempfile.TemporaryDirectory() as tmpdir:
            store = ResourceRequestStore(f"{tmpdir}/resource.db")
            request = store.create_request(
                feishu_user_id="ou_user",
                linux_username="zhangsan",
                project_name="客户验收",
                resource_type="K100",
                resource_amount=4,
                duration_hours=72,
                urgency="P1",
                deadline="",
                reason="精度测试",
                accept_queue=False,
                accept_downgrade=False,
                matched_pool_id="k100_train",
                priority_score=70,
                priority_reasons=["P1: +70"],
            )
            store.approve_request(request.request_code, approved_by="owner", duration_hours=48)
            loaded = store.get_request(request.request_code)

        self.assertEqual(loaded.status, "approved")
        self.assertEqual(loaded.duration_hours, 48)
        self.assertEqual(loaded.approved_by, "owner")

    def test_create_grant_plan_assigns_grant_code(self):
        from resource_request_store import ResourceRequestStore

        with tempfile.TemporaryDirectory() as tmpdir:
            store = ResourceRequestStore(f"{tmpdir}/resource.db")
            request = store.create_request(
                feishu_user_id="ou_user",
                linux_username="zhangsan",
                project_name="客户验收",
                resource_type="K100",
                resource_amount=4,
                duration_hours=72,
                urgency="P1",
                deadline="",
                reason="精度测试",
                accept_queue=False,
                accept_downgrade=False,
                matched_pool_id="k100_train",
                priority_score=70,
                priority_reasons=["P1: +70"],
            )
            grant = store.create_grant_plan(
                request_code=request.request_code,
                linux_username="zhangsan",
                pool_id="k100_train",
                target_nodes=["node01", "node02"],
                sshuser_path="/public/bin/sshuser",
                duration_hours=72,
                planned_by="owner",
            )
            loaded_request = store.get_request(request.request_code)

        self.assertEqual(grant.grant_code, "G1")
        self.assertEqual(grant.status, "planned")
        self.assertEqual(grant.target_nodes, ["node01", "node02"])
        self.assertEqual(grant.sshuser_path, "/public/bin/sshuser")
        self.assertEqual(loaded_request.status, "planned")

    def test_create_grant_plan_creates_per_node_rows(self):
        from resource_request_store import ResourceRequestStore

        with tempfile.TemporaryDirectory() as tmpdir:
            store = ResourceRequestStore(f"{tmpdir}/resource.db")
            request = store.create_request(
                feishu_user_id="ou_user",
                linux_username="zhangsan",
                project_name="客户验收",
                resource_type="K100",
                resource_amount=4,
                duration_hours=72,
                urgency="P1",
                deadline="",
                reason="精度测试",
                accept_queue=False,
                accept_downgrade=False,
                matched_pool_id="k100_train",
                priority_score=70,
                priority_reasons=["P1: +70"],
            )
            grant = store.create_grant_plan(
                request_code=request.request_code,
                linux_username="zhangsan",
                pool_id="k100_train",
                target_nodes=["node01", "node02"],
                sshuser_path="/public/bin/sshuser",
                duration_hours=72,
                planned_by="owner",
            )
            nodes = store.list_grant_nodes(grant.grant_code)

        self.assertEqual([node.node for node in nodes], ["node01", "node02"])
        self.assertEqual([node.grant_status for node in nodes], ["planned", "planned"])
        self.assertEqual([node.revoke_status for node in nodes], ["not_due", "not_due"])
        self.assertEqual(nodes[0].access_check_status, "unchecked")
        self.assertEqual(nodes[0].linux_username, "zhangsan")

    def test_update_grant_node_grant_result(self):
        from resource_request_store import ResourceRequestStore

        with tempfile.TemporaryDirectory() as tmpdir:
            store = ResourceRequestStore(f"{tmpdir}/resource.db")
            grant = self._create_grant(store)
            store.update_grant_node_grant_result(
                grant.grant_code,
                "node01",
                access_existed_before=False,
                access_check_status="absent",
                grant_status="succeeded",
                grant_last_error="",
            )
            nodes = store.list_grant_nodes(grant.grant_code)

        node01 = [node for node in nodes if node.node == "node01"][0]
        self.assertEqual(node01.grant_status, "succeeded")
        self.assertEqual(node01.access_check_status, "absent")
        self.assertEqual(node01.grant_attempts, 1)
        self.assertTrue(node01.granted_at)

    def test_claim_grant_for_update_changes_status_once(self):
        from resource_request_store import ResourceRequestStore

        with tempfile.TemporaryDirectory() as tmpdir:
            store = ResourceRequestStore(f"{tmpdir}/resource.db")
            grant = self._create_grant(store)
            first = store.claim_grant_status(grant.grant_code, ["planned"], "granting", actor="owner")
            second = store.claim_grant_status(grant.grant_code, ["planned"], "granting", actor="owner")
            loaded = store.get_grant(grant.grant_code)

        self.assertTrue(first)
        self.assertFalse(second)
        self.assertEqual(loaded.status, "granting")
        self.assertEqual(loaded.confirmed_by, "owner")

    def _create_grant(self, store):
        request = store.create_request(
            feishu_user_id="ou_user",
            linux_username="zhangsan",
            project_name="客户验收",
            resource_type="K100",
            resource_amount=4,
            duration_hours=72,
            urgency="P1",
            deadline="",
            reason="精度测试",
            accept_queue=False,
            accept_downgrade=False,
            matched_pool_id="k100_train",
            priority_score=70,
            priority_reasons=["P1: +70"],
        )
        return store.create_grant_plan(
            request_code=request.request_code,
            linux_username="zhangsan",
            pool_id="k100_train",
            target_nodes=["node01", "node02"],
            sshuser_path="/public/bin/sshuser",
            duration_hours=72,
            planned_by="owner",
        )


if __name__ == "__main__":
    unittest.main()
