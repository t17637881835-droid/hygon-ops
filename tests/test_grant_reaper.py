import sys
import tempfile
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
FEISHU_OPS = PROJECT_ROOT / "feishu_ops"
if str(FEISHU_OPS) not in sys.path:
    sys.path.insert(0, str(FEISHU_OPS))


class GrantReaperTests(unittest.TestCase):
    def tearDown(self):
        for name in ["grant_reaper", "resource_request_store", "sshuser_executor", "sshuser_grant_service", "sshuser_safety"]:
            sys.modules.pop(name, None)

    def test_reaper_revokes_due_grant(self):
        from grant_reaper import GrantReaper
        from resource_request_store import ResourceRequestStore
        from sshuser_executor import FakeSshuserExecutor
        from sshuser_grant_service import SshuserGrantService

        with tempfile.TemporaryDirectory() as tmpdir:
            store = ResourceRequestStore(f"{tmpdir}/resource.db")
            grant = self._create_grant(store)
            executor = FakeSshuserExecutor(access_by_node={"node01": False, "node02": False})
            service = SshuserGrantService(store, executor, {"node01", "node02"}, "/public/bin/sshuser")
            service.confirm_grant(grant.grant_code, actor="ou_owner")
            store.force_grant_valid_until(grant.grant_code, (datetime.now(UTC) - timedelta(minutes=1)).isoformat())
            executor.access_by_node = {"node01": True, "node02": True}
            reaper = GrantReaper(store, service)

            results = reaper.revoke_due_grants()
            loaded = store.get_grant(grant.grant_code)

        self.assertEqual([result.status for result in results], ["revoked"])
        self.assertEqual(loaded.status, "revoked")

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
