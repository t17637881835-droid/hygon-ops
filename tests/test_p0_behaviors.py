import importlib
import os
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

PROJECT_ROOT = Path(__file__).resolve().parents[1]
FEISHU_OPS = PROJECT_ROOT / "feishu_ops"
if str(FEISHU_OPS) not in sys.path:
    sys.path.insert(0, str(FEISHU_OPS))


class P0BehaviorTests(unittest.TestCase):
    def tearDown(self):
        for name in [
            "main",
            "knowledge_search",
            "knowledge_retriever",
            "skill_invoker",
            "config",
            "owner_notifier",
            "feishu_event_parser",
        ]:
            sys.modules.pop(name, None)
        os.environ.pop("KNOWLEDGE_RETRIEVER_TYPE", None)
        os.environ.pop("RAGFLOW_API_URL", None)
        os.environ.pop("RAGFLOW_API_TOKEN", None)
        os.environ.pop("HAIGUANG_OPS_SKIP_CONFIG_CHECK", None)

    def test_metrics_collector_exports_incrementable_metrics_object(self):
        metrics_collector = importlib.import_module("metrics_collector")
        self.assertTrue(hasattr(metrics_collector.metrics, "increment"))

    def test_knowledge_search_uses_configured_base_path(self):
        with TemporaryDirectory() as tmp:
            service_module = importlib.import_module("knowledge_search")
            service = service_module.KnowledgeSearchService(tmp)
            self.assertEqual(Path(service.retriever._backend.base_path), Path(tmp))

    def test_ragflow_placeholder_falls_back_to_empty_results_not_exception(self):
        os.environ["KNOWLEDGE_RETRIEVER_TYPE"] = "ragflow"
        os.environ["RAGFLOW_API_URL"] = "http://ragflow.example"
        os.environ["RAGFLOW_API_TOKEN"] = "token"
        retriever_module = importlib.import_module("knowledge_retriever")
        retriever = retriever_module.create_retriever("")
        self.assertEqual(retriever.search("驱动怎么装"), [])

    def test_message_queue_can_cancel_pending_messages_by_chat(self):
        queue_module = importlib.import_module("message_queue")
        queue = queue_module.MessageQueue(timeout_seconds=1)
        queue.add("m1", "u1", "chat-a", "驱动怎么装")
        queue.add("m2", "u2", "chat-b", "网络不通")
        cancelled = queue.cancel_by_chat("chat-a", "manual reply")
        self.assertEqual(cancelled, 1)
        self.assertNotIn("m1", queue._queue)
        self.assertIn("m2", queue._queue)

    def test_message_queue_persists_pending_messages_to_sqlite(self):
        queue_module = importlib.import_module("message_queue")
        with TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "queue.db"
            queue = queue_module.MessageQueue(timeout_seconds=600, db_path=str(db_path))
            queue.add("m1", "u1", "chat-a", "驱动怎么装")

            restored = queue_module.MessageQueue(timeout_seconds=600, db_path=str(db_path))
            self.assertIn("m1", restored._queue)
            self.assertEqual(restored._queue["m1"].content, "驱动怎么装")

    def test_message_queue_restores_timeout_messages_from_sqlite(self):
        queue_module = importlib.import_module("message_queue")
        with TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "queue.db"
            queue = queue_module.MessageQueue(timeout_seconds=1, db_path=str(db_path))
            queue.add("m1", "u1", "chat-a", "驱动怎么装")
            queue._queue["m1"].timestamp = 0
            queue._sync_message(queue._queue["m1"])

            restored = queue_module.MessageQueue(timeout_seconds=1, db_path=str(db_path))
            timeout_messages = restored.get_timeout_messages()
            self.assertEqual([msg.message_id for msg in timeout_messages], ["m1"])

    def test_feishu_event_parser_extracts_message_and_manual_reply(self):
        parser = importlib.import_module("feishu_event_parser")
        event = {
            "schema": "2.0",
            "header": {"event_type": "im.message.receive_v1"},
            "event": {
                "sender": {"sender_id": {"user_id": "ou_human"}},
                "message": {
                    "message_id": "om_1",
                    "chat_id": "oc_1",
                    "message_type": "text",
                    "content": "{\"text\": \"驱动怎么装\"}",
                },
            },
        }
        parsed = parser.parse_feishu_event(event, owner_user_ids={"ou_owner"}, bot_user_ids={"ou_bot"})
        self.assertEqual(parsed.action, "enqueue")
        self.assertEqual(parsed.content, "驱动怎么装")

        event["event"]["sender"]["sender_id"]["user_id"] = "ou_owner"
        parsed = parser.parse_feishu_event(event, owner_user_ids={"ou_owner"}, bot_user_ids={"ou_bot"})
        self.assertEqual(parsed.action, "cancel")

    def test_owner_cancel_command_targets_specified_chat_not_owner_chat(self):
        parser = importlib.import_module("feishu_event_parser")
        # owner 向 bot 发送“取消 oc_user_chat”，应取消 oc_user_chat，而非 owner-bot 对话的 oc_owner_bot
        event = {
            "schema": "2.0",
            "header": {"event_type": "im.message.receive_v1"},
            "event": {
                "sender": {"sender_id": {"user_id": "ou_owner"}},
                "message": {
                    "message_id": "om_cmd",
                    "chat_id": "oc_owner_bot",
                    "message_type": "text",
                    "content": '{"text": "\u53d6\u6d88 oc_user_chat"}',
                },
            },
        }
        parsed = parser.parse_feishu_event(event, owner_user_ids={"ou_owner"}, bot_user_ids={"ou_bot"})
        self.assertEqual(parsed.action, "cancel")
        self.assertEqual(parsed.chat_id, "oc_user_chat")
        self.assertEqual(parsed.reason, "owner_cancel_command")

    def test_owner_cancel_without_target_falls_back_to_current_chat(self):
        parser = importlib.import_module("feishu_event_parser")
        event = {
            "schema": "2.0",
            "header": {"event_type": "im.message.receive_v1"},
            "event": {
                "sender": {"sender_id": {"user_id": "ou_owner"}},
                "message": {
                    "message_id": "om_2",
                    "chat_id": "oc_group",
                    "message_type": "text",
                    "content": '{"text": "\u5df2\u5904\u7406"}',
                },
            },
        }
        parsed = parser.parse_feishu_event(event, owner_user_ids={"ou_owner"}, bot_user_ids={"ou_bot"})
        self.assertEqual(parsed.action, "cancel")
        self.assertEqual(parsed.chat_id, "oc_group")
        self.assertEqual(parsed.reason, "owner_replied")

    def test_owner_notifier_sends_to_owner_open_id(self):
        import types
        sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "feishu_ops"))
        fake_logger_module = types.ModuleType("logger")
        fake_logger_module.get_logger = lambda name=None: types.SimpleNamespace(
            info=lambda *a, **k: None,
            warning=lambda *a, **k: None,
            error=lambda *a, **k: None,
        )
        sys.modules["logger"] = fake_logger_module
        owner_notifier_mod = importlib.import_module("owner_notifier")

        calls = []
        fake_sender = types.SimpleNamespace(
            send_text=lambda content, chat_id=None, receive_id_type="chat_id": calls.append(
                {"content": content, "chat_id": chat_id, "receive_id_type": receive_id_type}
            ) or True
        )
        notifier = owner_notifier_mod.OwnerNotifier(fake_sender, {"ou_owner1", "ou_owner2"}, timeout_minutes=5)
        notifier.notify("ou_user", "oc_user_chat", "GPU 无法识别", short_id=7, urgent=False)

        self.assertEqual(len(calls), 2)
        for call in calls:
            self.assertEqual(call["receive_id_type"], "open_id")
            self.assertIn("ou_owner", call["chat_id"])
            self.assertIn("#7", call["content"])
            self.assertIn("/reply 7", call["content"])
            self.assertIn("/skip 7", call["content"])
            self.assertIn("/auto 7", call["content"])

    def test_owner_notifier_marks_urgent_messages(self):
        import types
        sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "feishu_ops"))
        fake_logger_module = types.ModuleType("logger")
        fake_logger_module.get_logger = lambda name=None: types.SimpleNamespace(
            info=lambda *a, **k: None, warning=lambda *a, **k: None, error=lambda *a, **k: None,
        )
        sys.modules["logger"] = fake_logger_module
        owner_notifier_mod = importlib.import_module("owner_notifier")

        calls = []
        fake_sender = types.SimpleNamespace(
            send_text=lambda content, chat_id=None, receive_id_type="chat_id": calls.append(content) or True
        )
        notifier = owner_notifier_mod.OwnerNotifier(fake_sender, {"ou_owner"}, timeout_minutes=10)
        notifier.notify("ou_user", "oc_user", "线上挂了", short_id=1, urgent=True)
        self.assertIn("🚨", calls[0])
        self.assertIn("紧急", calls[0])

    def test_owner_forward_command_extracts_short_id_and_content(self):
        parser = importlib.import_module("feishu_event_parser")
        event = {
            "schema": "2.0",
            "header": {"event_type": "im.message.receive_v1"},
            "event": {
                "sender": {"sender_id": {"user_id": "ou_owner"}},
                "message": {
                    "message_id": "om_fwd",
                    "chat_id": "oc_owner_bot",
                    "message_type": "text",
                    "content": '{"text": "/reply 3 \u8bf7\u68c0\u67e5\u516c\u94a5\u6743\u9650 700/600"}',
                },
            },
        }
        parsed = parser.parse_feishu_event(event, owner_user_ids={"ou_owner"}, bot_user_ids={"ou_bot"})
        self.assertEqual(parsed.action, "forward")
        self.assertEqual(parsed.short_id, 3)
        self.assertEqual(parsed.content, "请检查公钥权限 700/600")

    def test_owner_skip_and_auto_commands(self):
        parser = importlib.import_module("feishu_event_parser")
        for text, expected in [("/skip 5", "cancel_by_short_id"), ("/auto 7", "trigger_auto")]:
            event = {
                "schema": "2.0",
                "header": {"event_type": "im.message.receive_v1"},
                "event": {
                    "sender": {"sender_id": {"user_id": "ou_owner"}},
                    "message": {
                        "message_id": "om_x",
                        "chat_id": "oc_owner_bot",
                        "message_type": "text",
                        "content": '{"text": "%s"}' % text,
                    },
                },
            }
            parsed = parser.parse_feishu_event(event, owner_user_ids={"ou_owner"}, bot_user_ids={"ou_bot"})
            self.assertEqual(parsed.action, expected, f"failed for {text}")

    def test_urgent_keyword_marked_on_enqueue(self):
        parser = importlib.import_module("feishu_event_parser")
        event = {
            "schema": "2.0",
            "header": {"event_type": "im.message.receive_v1"},
            "event": {
                "sender": {"sender_id": {"user_id": "ou_user"}},
                "message": {
                    "message_id": "om_urg",
                    "chat_id": "oc_user",
                    "message_type": "text",
                    "content": '{"text": "\u7ebf\u4e0a\u6302\u4e86\uff0c\u5b95\u673a\uff01"}',
                },
            },
        }
        parsed = parser.parse_feishu_event(event, owner_user_ids={"ou_owner"}, bot_user_ids={"ou_bot"})
        self.assertEqual(parsed.action, "enqueue")
        self.assertTrue(parsed.urgent)

    def test_message_queue_assigns_sequential_short_ids(self):
        queue_module = importlib.import_module("message_queue")
        queue = queue_module.MessageQueue(timeout_seconds=600)
        msg1 = queue.add("m1", "u1", "chat-a", "Q1")
        msg2 = queue.add("m2", "u2", "chat-b", "Q2")
        self.assertEqual(msg1.short_id, 1)
        self.assertEqual(msg2.short_id, 2)
        self.assertIs(queue.get_by_short_id(1), msg1)
        self.assertIs(queue.get_by_short_id(2), msg2)

    def test_message_queue_force_timeout_sets_timestamp_zero(self):
        queue_module = importlib.import_module("message_queue")
        queue = queue_module.MessageQueue(timeout_seconds=600)
        msg = queue.add("m1", "u1", "chat-a", "Q1")
        self.assertGreater(msg.timestamp, 0)
        forced = queue.force_timeout(msg.short_id)
        self.assertIsNotNone(forced)
        self.assertEqual(forced.timestamp, 0)

    def test_high_confidence_kb_returns_solution_without_llm(self):
        skill_invoker = importlib.import_module("skill_invoker")
        invoker = skill_invoker.SkillInvoker()
        invoker.knowledge_search.search = lambda question, limit=3: [
            {"score": 0.95, "solution": "\u76f4\u63a5\u8d70 FAQ \u7684\u7b54\u6848", "category": "gpu", "question": "Q"}
        ]
        invoker._call_anthropic = lambda prompt: self.fail("LLM \u4e0d\u5e94\u88ab\u8c03\u7528\uff08\u9ad8\u7f6e\u4fe1\u5ea6\u5feb\u8def\uff09")
        result = invoker.invoke("\u67d0\u4e2a\u95ee\u9898")
        self.assertTrue(result["success"])
        self.assertEqual(result["response"], "\u76f4\u63a5\u8d70 FAQ \u7684\u7b54\u6848")
        self.assertTrue(result["from_kb"])

    def test_low_confidence_invocation_returns_busy_reply_without_llm(self):
        skill_invoker = importlib.import_module("skill_invoker")
        invoker = skill_invoker.SkillInvoker()
        invoker.knowledge_search.search = lambda question, limit=3: []
        invoker._call_anthropic = lambda prompt: self.fail("LLM should not be called for low-confidence results")
        result = invoker.invoke("一个知识库没有的问题")
        self.assertTrue(result["success"])
        self.assertEqual(result["response"], invoker.config.skill.busy_reply)
        self.assertTrue(result["low_confidence"])

    def test_timeout_processing_removes_message_and_sends_to_chat(self):
        os.environ["HAIGUANG_OPS_SKIP_CONFIG_CHECK"] = "1"
        try:
            main = importlib.import_module("main")
        except ModuleNotFoundError as exc:
            if exc.name == "fastapi":
                self.skipTest("fastapi is not installed in this local Python environment")
            raise
        sent = []
        main.message_queue.add("m-timeout", "u1", "chat-1", "驱动怎么装")
        main.message_queue._queue["m-timeout"].timestamp = 0
        main.skill_invoker.invoke = lambda content: {"success": True, "response": "安装步骤"}
        main.feishu_sender.send_text = lambda content, chat_id=None: sent.append((content, chat_id)) or True
        main.check_timeout_messages()
        self.assertNotIn("m-timeout", main.message_queue._queue)
        self.assertEqual(sent, [("安装步骤", "chat-1")])


if __name__ == "__main__":
    unittest.main()
