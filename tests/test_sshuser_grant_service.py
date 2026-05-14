import sys
import tempfile
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
FEISHU_OPS = PROJECT_ROOT / "feishu_ops"
if str(FEISHU_OPS) not in sys.path:
    sys.path.insert(0, str(FEISHU_OPS))


class SshuserGrantServiceTests(unittest.TestCase):
    def tearDown(self):
        for name in ["sshuser_executor", "sshuser_grant_service", "sshuser_safety", "resource_request_store"]:
            sys.modules.pop(name, None)

    def test_fake_executor_returns_configured_access_result(self):
        from sshuser_executor import FakeSshuserExecutor

        executor = FakeSshuserExecutor(access_by_node={"node01": True, "node02": False})

        self.assertTrue(executor.check_access("node01", "zhangsan", "/public/bin/sshuser").present)
        self.assertFalse(executor.check_access("node02", "zhangsan", "/public/bin/sshuser").present)

    def test_confirm_grant_adds_absent_user_and_skips_preexisting(self):
        from resource_request_store import ResourceRequestStore
        from sshuser_executor import FakeSshuserExecutor
        from sshuser_grant_service import SshuserGrantService

        with tempfile.TemporaryDirectory() as tmpdir:
            store = ResourceRequestStore(f"{tmpdir}/resource.db")
            grant = self._create_grant(store)
            executor = FakeSshuserExecutor(access_by_node={"node01": False, "node02": True})
            service = SshuserGrantService(
                store=store,
                executor=executor,
                allowed_nodes={"node01", "node02"},
                configured_sshuser_path="/public/bin/sshuser",
            )

            result = service.confirm_grant(grant.grant_code, actor="ou_owner")
            nodes = store.list_grant_nodes(grant.grant_code)
            loaded = store.get_grant(grant.grant_code)

        by_node = {node.node: node for node in nodes}
        self.assertEqual(result.status, "granted")
        self.assertEqual(loaded.status, "granted")
        self.assertEqual(by_node["node01"].grant_status, "succeeded")
        self.assertEqual(by_node["node02"].grant_status, "skipped_preexisting")
        self.assertIn(("add", "node01", "zhangsan", "/public/bin/sshuser"), executor.calls)
        self.assertNotIn(("add", "node02", "zhangsan", "/public/bin/sshuser"), executor.calls)

    def test_confirm_grant_records_partial_failure(self):
        from resource_request_store import ResourceRequestStore
        from sshuser_executor import FakeSshuserExecutor
        from sshuser_grant_service import SshuserGrantService

        with tempfile.TemporaryDirectory() as tmpdir:
            store = ResourceRequestStore(f"{tmpdir}/resource.db")
            grant = self._create_grant(store)
            executor = FakeSshuserExecutor(
                access_by_node={"node01": False, "node02": False},
                grant_success_by_node={"node01": True, "node02": False},
            )
            service = SshuserGrantService(store, executor, {"node01", "node02"}, "/public/bin/sshuser")

            result = service.confirm_grant(grant.grant_code, actor="ou_owner")
            nodes = store.list_grant_nodes(grant.grant_code)

        by_node = {node.node: node for node in nodes}
        self.assertEqual(result.status, "partial_granted")
        self.assertEqual(by_node["node01"].grant_status, "succeeded")
        self.assertEqual(by_node["node02"].grant_status, "failed")

    def test_second_active_grant_is_covered_by_first_and_last_grant_revokes(self):
        from resource_request_store import ResourceRequestStore
        from sshuser_executor import FakeSshuserExecutor
        from sshuser_grant_service import SshuserGrantService

        with tempfile.TemporaryDirectory() as tmpdir:
            store = ResourceRequestStore(f"{tmpdir}/resource.db")
            first = self._create_grant(store)
            second = self._create_grant(store)
            executor = FakeSshuserExecutor(access_by_node={"node01": False, "node02": False})
            service = SshuserGrantService(store, executor, {"node01", "node02"}, "/public/bin/sshuser")

            service.confirm_grant(first.grant_code, actor="ou_owner")
            executor.access_by_node = {"node01": True, "node02": True}
            service.confirm_grant(second.grant_code, actor="ou_owner")
            second_nodes = store.list_grant_nodes(second.grant_code)

        self.assertEqual({node.grant_status for node in second_nodes}, {"covered_by_active_grant"})

    def test_revoke_skips_preexisting_and_deletes_system_added_access(self):
        from resource_request_store import ResourceRequestStore
        from sshuser_executor import FakeSshuserExecutor
        from sshuser_grant_service import SshuserGrantService

        with tempfile.TemporaryDirectory() as tmpdir:
            store = ResourceRequestStore(f"{tmpdir}/resource.db")
            grant = self._create_grant(store)
            executor = FakeSshuserExecutor(access_by_node={"node01": False, "node02": True})
            service = SshuserGrantService(store, executor, {"node01", "node02"}, "/public/bin/sshuser")
            service.confirm_grant(grant.grant_code, actor="ou_owner")
            executor.access_by_node = {"node01": True, "node02": True}

            result = service.revoke_grant(grant.grant_code, actor="reaper")
            nodes = store.list_grant_nodes(grant.grant_code)

        by_node = {node.node: node for node in nodes}
        self.assertEqual(result.status, "revoked")
        self.assertEqual(by_node["node01"].revoke_status, "succeeded")
        self.assertEqual(by_node["node02"].revoke_status, "skipped_preexisting")
        self.assertIn(("del", "node01", "zhangsan", "/public/bin/sshuser"), executor.calls)
        self.assertNotIn(("del", "node02", "zhangsan", "/public/bin/sshuser"), executor.calls)

    def test_revoke_skips_when_other_active_grant_exists(self):
        from resource_request_store import ResourceRequestStore
        from sshuser_executor import FakeSshuserExecutor
        from sshuser_grant_service import SshuserGrantService

        with tempfile.TemporaryDirectory() as tmpdir:
            store = ResourceRequestStore(f"{tmpdir}/resource.db")
            first = self._create_grant(store)
            second = self._create_grant(store)
            executor = FakeSshuserExecutor(access_by_node={"node01": False, "node02": False})
            service = SshuserGrantService(store, executor, {"node01", "node02"}, "/public/bin/sshuser")
            service.confirm_grant(first.grant_code, actor="ou_owner")
            executor.access_by_node = {"node01": True, "node02": True}
            service.confirm_grant(second.grant_code, actor="ou_owner")

            result = service.revoke_grant(first.grant_code, actor="reaper")
            nodes = store.list_grant_nodes(first.grant_code)

        self.assertEqual(result.status, "revoked")
        self.assertEqual({node.revoke_status for node in nodes}, {"skipped_active_grant"})

    def test_mark_revoke_done_marks_only_selected_nodes(self):
        from resource_request_store import ResourceRequestStore
        from sshuser_executor import FakeSshuserExecutor
        from sshuser_grant_service import SshuserGrantService

        with tempfile.TemporaryDirectory() as tmpdir:
            store = ResourceRequestStore(f"{tmpdir}/resource.db")
            grant = self._create_grant(store)
            executor = FakeSshuserExecutor(access_by_node={"node01": False, "node02": False}, revoke_success_by_node={"node01": False, "node02": False})
            service = SshuserGrantService(store, executor, {"node01", "node02"}, "/public/bin/sshuser")
            service.confirm_grant(grant.grant_code, actor="ou_owner")
            executor.access_by_node = {"node01": True, "node02": True}
            service.revoke_grant(grant.grant_code, actor="reaper")

            result = service.mark_revoke_done(grant.grant_code, ["node01"], actor="ou_owner")
            nodes = store.list_grant_nodes(grant.grant_code)

        by_node = {node.node: node for node in nodes}
        self.assertEqual(by_node["node01"].revoke_status, "succeeded_manual")
        self.assertEqual(by_node["node02"].revoke_status, "failed")
        self.assertEqual(result.status, "partial_revoked")

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
