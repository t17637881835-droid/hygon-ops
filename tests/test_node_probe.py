import importlib
import sys
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
FEISHU_OPS = PROJECT_ROOT / "feishu_ops"
if str(FEISHU_OPS) not in sys.path:
    sys.path.insert(0, str(FEISHU_OPS))


class Completed:
    def __init__(self, returncode=0, stdout="", stderr=""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


class NodeProbeTests(unittest.TestCase):
    def tearDown(self):
        sys.modules.pop("node_probe", None)
        sys.modules.pop("feishu_event_parser", None)

    def test_owner_ping_command_is_parsed(self):
        parser = importlib.import_module("feishu_event_parser")
        event = _event("ou_owner", "/ping")

        parsed = parser.parse_feishu_event(event, owner_user_ids={"ou_owner"}, bot_user_ids={"ou_bot"})

        self.assertEqual(parsed.action, "ping")
        self.assertEqual(parsed.content, "/ping")

    def test_owner_node_status_command_is_parsed(self):
        parser = importlib.import_module("feishu_event_parser")
        event = _event("ou_owner", "/node status local")

        parsed = parser.parse_feishu_event(event, owner_user_ids={"ou_owner"}, bot_user_ids={"ou_bot"})

        self.assertEqual(parsed.action, "node_owner_command")
        self.assertEqual(parsed.content, "/node status local")

    def test_local_status_uses_whitelisted_commands(self):
        calls = []

        def runner(command, **kwargs):
            calls.append(command)
            return Completed(stdout="ok\n")

        node_probe = importlib.import_module("node_probe")
        probe = node_probe.LocalNodeProbe(command_runner=runner)

        result = probe.status("local")

        self.assertTrue(result.success)
        self.assertEqual(calls, [["hostname"], ["date", "-Is"], ["uptime"]])
        self.assertIn("hostname: ok", result.output)

    def test_remote_node_is_rejected(self):
        node_probe = importlib.import_module("node_probe")
        probe = node_probe.LocalNodeProbe(command_runner=lambda *args, **kwargs: Completed(stdout="ok\n"))

        result = probe.status("node01")

        self.assertFalse(result.success)
        self.assertIn("only local node", result.error)


def _event(user_id, text):
    return {
        "schema": "2.0",
        "header": {"event_type": "im.message.receive_v1"},
        "event": {
            "sender": {"sender_id": {"user_id": user_id}},
            "message": {
                "message_id": "om_node",
                "chat_id": "oc_owner_bot",
                "message_type": "text",
                "content": '{"text": "%s"}' % text,
            },
        },
    }


if __name__ == "__main__":
    unittest.main()
