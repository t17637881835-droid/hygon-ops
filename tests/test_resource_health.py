import asyncio
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


class ResourceHealthTests(unittest.TestCase):
    def tearDown(self):
        for name in ["main", "config", "fastapi", "logger", "uvicorn"]:
            sys.modules.pop(name, None)
        for name in [
            "RESOURCE_REQUEST_ENABLED",
            "RESOURCE_POOLS_CONFIG_PATH",
            "RESOURCE_REQUEST_DB_PATH",
            "SSHUSER_GRANT_ENABLED",
            "SSHUSER_REMOTE_EXEC_ENABLED",
            "SSHUSER_JUMP_HOST",
            "SSHUSER_JUMP_USER",
            "SSHUSER_SSH_KEY_PATH",
            "SSHUSER_KNOWN_HOSTS_PATH",
            "SSHUSER_TARGET_USER",
        ]:
            os.environ.pop(name, None)

    def test_health_reports_resource_workflow_status(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            pools_path = Path(tmpdir) / "resource_pools.yml"
            pools_path.write_text(textwrap.dedent("""
                resource_pools:
                  - pool_id: k100_train
                    name: K100-训练池
                    resource_type: K100
                    nodes: [node01]
                    sshuser_path: /public/bin/sshuser
                    total_devices: 8
                    default_grant_hours: 24
                    max_grant_hours: 72
                    enabled: true
            """), encoding="utf-8")
            os.environ["RESOURCE_REQUEST_ENABLED"] = "true"
            os.environ["RESOURCE_POOLS_CONFIG_PATH"] = str(pools_path)
            os.environ["RESOURCE_REQUEST_DB_PATH"] = str(Path(tmpdir) / "resource.db")
            os.environ["SSHUSER_GRANT_ENABLED"] = "false"
            self._install_runtime_stubs()
            main = importlib.import_module("main")

            payload = asyncio.run(main.health())

        self.assertEqual(payload["resource_request"]["enabled"], True)
        self.assertEqual(payload["resource_request"]["pools_loaded"], 1)
        self.assertEqual(payload["resource_request"]["sshuser_grant_enabled"], False)
        self.assertEqual(payload["resource_request"]["mode"], "sshuser_advice_only")

    def test_health_reports_phase2_remote_exec_readiness(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            pools_path = Path(tmpdir) / "resource_pools.yml"
            pools_path.write_text(textwrap.dedent("""
                resource_pools:
                  - pool_id: k100_train
                    name: K100-训练池
                    resource_type: K100
                    nodes: [node01]
                    sshuser_path: /public/bin/sshuser
                    total_devices: 8
                    default_grant_hours: 24
                    max_grant_hours: 72
                    enabled: true
            """), encoding="utf-8")
            os.environ["RESOURCE_REQUEST_ENABLED"] = "true"
            os.environ["RESOURCE_POOLS_CONFIG_PATH"] = str(pools_path)
            os.environ["RESOURCE_REQUEST_DB_PATH"] = str(Path(tmpdir) / "resource.db")
            os.environ["SSHUSER_GRANT_ENABLED"] = "true"
            os.environ["SSHUSER_REMOTE_EXEC_ENABLED"] = "true"
            os.environ["SSHUSER_JUMP_HOST"] = "jump.example"
            os.environ["SSHUSER_JUMP_USER"] = "resource_bot"
            os.environ["SSHUSER_SSH_KEY_PATH"] = "/app/secrets/id_rsa"
            os.environ["SSHUSER_KNOWN_HOSTS_PATH"] = "/app/secrets/known_hosts"
            os.environ["SSHUSER_TARGET_USER"] = "resource_exec"
            self._install_runtime_stubs()
            main = importlib.import_module("main")

            payload = asyncio.run(main.health())

        resource = payload["resource_request"]
        self.assertEqual(resource["sshuser_remote_exec_enabled"], True)
        self.assertEqual(resource["mode"], "sshuser_mutation")
        self.assertEqual(resource["jump_host_configured"], True)
        self.assertEqual(resource["ssh_key_configured"], True)
        self.assertEqual(resource["known_hosts_configured"], True)

    def _install_runtime_stubs(self):
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
