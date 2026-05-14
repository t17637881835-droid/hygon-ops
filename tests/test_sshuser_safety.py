import sys
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
FEISHU_OPS = PROJECT_ROOT / "feishu_ops"
if str(FEISHU_OPS) not in sys.path:
    sys.path.insert(0, str(FEISHU_OPS))


class SshuserSafetyTests(unittest.TestCase):
    def tearDown(self):
        sys.modules.pop("sshuser_safety", None)

    def test_validate_linux_username_accepts_safe_names(self):
        from sshuser_safety import validate_linux_username

        self.assertEqual(validate_linux_username("zhangsan"), "zhangsan")
        self.assertEqual(validate_linux_username("_svc-user1"), "_svc-user1")

    def test_validate_linux_username_rejects_injection(self):
        from sshuser_safety import validate_linux_username, SshuserSafetyError

        for value in ["ZhangSan", "zhang san", "zhang;id", "$(id)", "../root", "张三"]:
            with self.subTest(value=value):
                with self.assertRaises(SshuserSafetyError):
                    validate_linux_username(value)

    def test_validate_node_accepts_only_allowed_safe_node(self):
        from sshuser_safety import validate_node, SshuserSafetyError

        self.assertEqual(validate_node("node01", {"node01", "node02"}), "node01")
        with self.assertRaises(SshuserSafetyError):
            validate_node("node03", {"node01", "node02"})
        with self.assertRaises(SshuserSafetyError):
            validate_node("node01;id", {"node01;id"})

    def test_validate_sshuser_path_requires_configured_absolute_sshuser(self):
        from sshuser_safety import validate_sshuser_path, SshuserSafetyError

        self.assertEqual(validate_sshuser_path("/public/bin/sshuser", "/public/bin/sshuser"), "/public/bin/sshuser")
        with self.assertRaises(SshuserSafetyError):
            validate_sshuser_path("/tmp/sshuser", "/public/bin/sshuser")
        with self.assertRaises(SshuserSafetyError):
            validate_sshuser_path("/public/bin/sshuser;id", "/public/bin/sshuser")

    def test_parse_allow_users(self):
        from sshuser_safety import parse_allow_users, SshuserSafetyError

        self.assertTrue(parse_allow_users("AllowUsers root zhangsan lisi\n", "zhangsan"))
        self.assertFalse(parse_allow_users("AllowUsers root lisi\n", "zhangsan"))
        with self.assertRaises(SshuserSafetyError):
            parse_allow_users("AllowUsers root\nAllowUsers zhangsan\n", "zhangsan")


if __name__ == "__main__":
    unittest.main()
