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


class ConfigCheckTests(unittest.TestCase):
    def tearDown(self):
        sys.modules.pop("config_check", None)

    def test_validate_env_reports_missing_required_variables(self):
        module = importlib.import_module("config_check")
        errors, warnings = module.validate_env({})
        self.assertIn("FEISHU_APP_ID is required", errors)
        self.assertIn("FEISHU_APP_SECRET is required", errors)
        self.assertIn("ANTHROPIC_API_KEY is required", errors)

    def test_validate_env_accepts_minimal_required_variables(self):
        module = importlib.import_module("config_check")
        with TemporaryDirectory() as tmp:
            kb_path = Path(tmp) / "knowledge_base"
            kb_path.mkdir()
            env = {
                "FEISHU_APP_ID": "app-id",
                "FEISHU_APP_SECRET": "secret",
                "ANTHROPIC_API_KEY": "key",
                "KNOWLEDGE_BASE_PATH": str(kb_path),
                "MESSAGE_QUEUE_DB_PATH": str(Path(tmp) / "data" / "queue.db"),
                "AUDIT_LOG_PATH": str(Path(tmp) / "logs" / "audit.jsonl"),
            }
            errors, warnings = module.validate_env(env)
            self.assertEqual(errors, [])

    def test_assert_valid_env_raises_for_missing_required_variables(self):
        module = importlib.import_module("config_check")
        with self.assertRaises(RuntimeError) as ctx:
            module.assert_valid_env({})
        self.assertIn("FEISHU_APP_ID is required", str(ctx.exception))

    def test_assert_valid_env_allows_minimal_required_variables(self):
        module = importlib.import_module("config_check")
        with TemporaryDirectory() as tmp:
            kb_path = Path(tmp) / "knowledge_base"
            kb_path.mkdir()
            env = {
                "FEISHU_APP_ID": "app-id",
                "FEISHU_APP_SECRET": "secret",
                "ANTHROPIC_API_KEY": "key",
                "KNOWLEDGE_BASE_PATH": str(kb_path),
                "MESSAGE_QUEUE_DB_PATH": str(Path(tmp) / "data" / "queue.db"),
                "AUDIT_LOG_PATH": str(Path(tmp) / "logs" / "audit.jsonl"),
            }
            module.assert_valid_env(env)

    def test_resource_pool_config_missing_is_reported_when_enabled(self):
        module = importlib.import_module("config_check")
        env = {
            "FEISHU_APP_ID": "app-id",
            "FEISHU_APP_SECRET": "secret",
            "ANTHROPIC_API_KEY": "key",
            "RESOURCE_REQUEST_ENABLED": "true",
            "RESOURCE_POOLS_CONFIG_PATH": "C:/path/does/not/exist.yml",
        }
        errors, warnings = module.validate_env(env)
        self.assertTrue(any("RESOURCE_POOLS_CONFIG_PATH" in item for item in errors))

    def test_sshuser_grant_enabled_warns_when_remote_exec_is_disabled(self):
        module = importlib.import_module("config_check")
        env = {
            "FEISHU_APP_ID": "app-id",
            "FEISHU_APP_SECRET": "secret",
            "ANTHROPIC_API_KEY": "key",
            "SSHUSER_GRANT_ENABLED": "true",
            "SSHUSER_REMOTE_EXEC_ENABLED": "false",
        }
        errors, warnings = module.validate_env(env)
        self.assertEqual(errors, [])
        self.assertTrue(any("SSHUSER_GRANT_ENABLED=true" in item for item in warnings))

    def test_remote_exec_enabled_requires_jump_host_settings(self):
        module = importlib.import_module("config_check")
        env = {
            "FEISHU_APP_ID": "app-id",
            "FEISHU_APP_SECRET": "secret",
            "ANTHROPIC_API_KEY": "key",
            "SSHUSER_GRANT_ENABLED": "true",
            "SSHUSER_REMOTE_EXEC_ENABLED": "true",
            "SSHUSER_COMMAND_PATH": "/public/bin/sshuser",
        }
        errors, warnings = module.validate_env(env)

        self.assertTrue(any("SSHUSER_JUMP_HOST" in item for item in errors))
        self.assertTrue(any("SSHUSER_JUMP_USER" in item for item in errors))
        self.assertTrue(any("SSHUSER_TARGET_USER" in item for item in errors))


if __name__ == "__main__":
    unittest.main()
