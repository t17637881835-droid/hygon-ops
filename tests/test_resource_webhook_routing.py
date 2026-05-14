import importlib
import os
import sys
import tempfile
import textwrap
import types
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
FEISHU_OPS = PROJECT_ROOT / "feishu_ops"
if str(FEISHU_OPS) not in sys.path:
    sys.path.insert(0, str(FEISHU_OPS))


class ResourceWebhookRoutingTests(unittest.TestCase):
    def tearDown(self):
        for name in [
            "main",
            "config",
            "feishu_event_parser",
            "resource_approval",
            "resource_config",
            "resource_pool",
            "resource_priority",
            "resource_prometheus",
            "resource_request_parser",
            "resource_request_store",
            "sshuser_executor",
            "sshuser_grant_service",
            "sshuser_safety",
            "fastapi",
            "logger",
            "uvicorn",
        ]:
            sys.modules.pop(name, None)
        for name in [
            "RESOURCE_REQUEST_ENABLED",
            "RESOURCE_POOLS_CONFIG_PATH",
            "RESOURCE_REQUEST_DB_PATH",
            "FEISHU_OWNER_USER_IDS",
            "HAIGUANG_OPS_SKIP_CONFIG_CHECK",
            "SSHUSER_GRANT_ENABLED",
            "SSHUSER_REMOTE_EXEC_ENABLED",
            "SSHUSER_EXECUTOR_TYPE",
        ]:
            os.environ.pop(name, None)

    def test_owner_approve_event_is_resource_owner_command(self):
        parser = importlib.import_module("feishu_event_parser")
        event = {
            "schema": "2.0",
            "header": {"event_type": "im.message.receive_v1"},
            "event": {
                "sender": {"sender_id": {"user_id": "ou_owner"}},
                "message": {
                    "message_id": "om_approve",
                    "chat_id": "oc_owner_bot",
                    "message_type": "text",
                    "content": '{"text": "/approve R1 72h"}',
                },
            },
        }

        parsed = parser.parse_feishu_event(event, owner_user_ids={"ou_owner"}, bot_user_ids={"ou_bot"})

        self.assertEqual(parsed.action, "resource_owner_command")
        self.assertEqual(parsed.content, "/approve R1 72h")

    def test_handle_resource_apply_creates_request_and_notifies_owner(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            self._configure_resource_env(tmpdir)
            self._install_fastapi_stub()
            main = importlib.import_module("main")
            parser = importlib.import_module("feishu_event_parser")
            sent = []
            main.feishu_sender.send_text = lambda content, chat_id=None, receive_id_type="chat_id": sent.append({
                "content": content,
                "chat_id": chat_id,
                "receive_id_type": receive_id_type,
            }) or True
            parsed = parser.ParsedFeishuEvent(
                action="enqueue",
                message_id="om_apply",
                user_id="ou_user",
                chat_id="oc_user",
                content="""
/apply
Linux账号：zhangsan
资源类型：K100
数量：4卡
使用时长：72小时
紧急程度：P1
项目：客户验收
用途：精度测试
是否接受排队：是
""",
            )

            result = main._handle_resource_apply(parsed)
            request = main.resource_request_store.get_request("R1")

        self.assertEqual(result["status"], "resource_request_created")
        self.assertEqual(request.linux_username, "zhangsan")
        self.assertEqual(request.matched_pool_id, "k100_train")
        self.assertTrue(any(call["chat_id"] == "oc_user" for call in sent))
        owner_messages = [call["content"] for call in sent if call["chat_id"] == "ou_owner"]
        self.assertEqual(len(owner_messages), 1)
        self.assertIn("/approve R1 72h", owner_messages[0])

    def test_handle_owner_approve_creates_phase1_grant_advice(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            self._configure_resource_env(tmpdir)
            self._install_fastapi_stub()
            main = importlib.import_module("main")
            parser = importlib.import_module("feishu_event_parser")
            sent = []
            main.feishu_sender.send_text = lambda content, chat_id=None, receive_id_type="chat_id": sent.append({
                "content": content,
                "chat_id": chat_id,
                "receive_id_type": receive_id_type,
            }) or True
            main.resource_request_store.create_request(
                feishu_user_id="ou_user",
                linux_username="zhangsan",
                project_name="客户验收",
                resource_type="K100",
                resource_amount=4,
                duration_hours=72,
                urgency="P1",
                deadline="",
                reason="精度测试",
                accept_queue=True,
                accept_downgrade=False,
                matched_pool_id="k100_train",
                priority_score=115,
                priority_reasons=["P1: +70"],
            )
            parsed = parser.ParsedFeishuEvent(
                action="resource_owner_command",
                user_id="ou_owner",
                chat_id="oc_owner_bot",
                content="/approve R1 48h",
            )

            result = main._handle_resource_owner_command(parsed)
            request = main.resource_request_store.get_request("R1")

        self.assertEqual(result["status"], "resource_approved")
        self.assertEqual(request.status, "planned")
        self.assertTrue(any("不会自动执行节点命令" in call["content"] for call in sent))
        self.assertTrue(any("node01: /public/bin/sshuser add zhangsan" in call["content"] for call in sent))
        self.assertFalse(any("LD" + "AP" in call["content"] for call in sent))
        self.assertTrue(any(call["chat_id"] == "ou_user" and call["receive_id_type"] == "open_id" for call in sent))

    def test_handle_grant_confirm_executes_phase2_service_when_remote_exec_enabled(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            self._configure_resource_env(tmpdir)
            os.environ["SSHUSER_GRANT_ENABLED"] = "true"
            os.environ["SSHUSER_REMOTE_EXEC_ENABLED"] = "true"
            os.environ["SSHUSER_EXECUTOR_TYPE"] = "fake"
            self._install_fastapi_stub()
            main = importlib.import_module("main")
            self.assertEqual(main.config.resource_request.sshuser_executor_type, "fake")
            self.assertIsNotNone(main.sshuser_grant_service)
            parser = importlib.import_module("feishu_event_parser")
            sent = []
            main.feishu_sender.send_text = lambda content, chat_id=None, receive_id_type="chat_id": sent.append({
                "content": content,
                "chat_id": chat_id,
                "receive_id_type": receive_id_type,
            }) or True
            main.resource_request_store.create_request(
                feishu_user_id="ou_user",
                linux_username="zhangsan",
                project_name="客户验收",
                resource_type="K100",
                resource_amount=4,
                duration_hours=72,
                urgency="P1",
                deadline="",
                reason="精度测试",
                accept_queue=True,
                accept_downgrade=False,
                matched_pool_id="k100_train",
                priority_score=115,
                priority_reasons=["P1: +70"],
            )
            approve = parser.ParsedFeishuEvent(action="resource_owner_command", user_id="ou_owner", chat_id="oc_owner_bot", content="/approve R1 48h")
            main._handle_resource_owner_command(approve)
            confirm = parser.ParsedFeishuEvent(action="resource_owner_command", user_id="ou_owner", chat_id="oc_owner_bot", content="/grant G1 confirm")

            result = main._handle_resource_owner_command(confirm)
            grant = main.resource_request_store.get_grant("G1")

        self.assertEqual(result["status"], "resource_grant_confirmed")
        self.assertEqual(grant.status, "granted")
        self.assertTrue(any("授权执行结果" in call["content"] for call in sent))

    def _configure_resource_env(self, tmpdir):
        pools_path = Path(tmpdir) / "resource_pools.yml"
        pools_path.write_text(textwrap.dedent("""
            resource_pools:
              - pool_id: k100_train
                name: K100-训练池
                resource_type: K100
                nodes: [node01, node02]
                sshuser_path: /public/bin/sshuser
                total_devices: 16
                default_grant_hours: 72
                max_grant_hours: 168
                enabled: true
        """), encoding="utf-8")
        os.environ["HAIGUANG_OPS_SKIP_CONFIG_CHECK"] = "1"
        os.environ["RESOURCE_REQUEST_ENABLED"] = "true"
        os.environ["RESOURCE_POOLS_CONFIG_PATH"] = str(pools_path)
        os.environ["RESOURCE_REQUEST_DB_PATH"] = str(Path(tmpdir) / "resource.db")
        os.environ["FEISHU_OWNER_USER_IDS"] = "ou_owner"

    def _install_fastapi_stub(self):
        class FakeFastAPI:
            def post(self, path):
                return lambda func: func

            def get(self, path):
                return lambda func: func

        fastapi = types.ModuleType("fastapi")
        fastapi.FastAPI = FakeFastAPI
        fastapi.Request = object
        fastapi.HTTPException = Exception
        sys.modules["fastapi"] = fastapi
        uvicorn = types.ModuleType("uvicorn")
        uvicorn.run = lambda *args, **kwargs: None
        sys.modules["uvicorn"] = uvicorn
        logger = types.ModuleType("logger")
        logger.get_logger = lambda name=None: types.SimpleNamespace(
            info=lambda *args, **kwargs: None,
            warning=lambda *args, **kwargs: None,
            error=lambda *args, **kwargs: None,
        )
        sys.modules["logger"] = logger


if __name__ == "__main__":
    unittest.main()
