import sys
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
FEISHU_OPS = PROJECT_ROOT / "feishu_ops"
if str(FEISHU_OPS) not in sys.path:
    sys.path.insert(0, str(FEISHU_OPS))


class JumpHostExecutorTests(unittest.TestCase):
    def tearDown(self):
        sys.modules.pop("jump_host_executor", None)
        sys.modules.pop("sshuser_executor", None)

    def test_builds_nested_sshuser_add_command(self):
        from jump_host_executor import JumpHostSshExecutor

        executor = JumpHostSshExecutor(
            jump_host="jump.example",
            jump_port=22,
            jump_user="resource_bot",
            ssh_key_path="/app/secrets/id_rsa",
            known_hosts_path="/app/secrets/known_hosts",
            target_user="resource_exec",
            target_port=22,
            connect_timeout_seconds=5,
            command_timeout_seconds=15,
        )

        command = executor.build_command("node01", "add", "zhangsan", "/public/bin/sshuser")

        joined = " ".join(command)
        self.assertIn("BatchMode=yes", joined)
        self.assertIn("StrictHostKeyChecking=yes", joined)
        self.assertIn("resource_bot@jump.example", joined)
        self.assertIn("resource_exec@node01", joined)
        self.assertIn("sudo /public/bin/sshuser add zhangsan", joined)
        self.assertNotIn(";", joined)

    def test_rejects_unsafe_node_before_nested_shell_command(self):
        from jump_host_executor import JumpHostSshExecutor

        executor = JumpHostSshExecutor(
            jump_host="jump.example",
            jump_port=22,
            jump_user="resource_bot",
            ssh_key_path="/app/secrets/id_rsa",
            known_hosts_path="/app/secrets/known_hosts",
            target_user="resource_exec",
            target_port=22,
            connect_timeout_seconds=5,
            command_timeout_seconds=15,
        )

        with self.assertRaises(ValueError):
            executor.build_command("node01;id", "add", "zhangsan", "/public/bin/sshuser")


if __name__ == "__main__":
    unittest.main()
