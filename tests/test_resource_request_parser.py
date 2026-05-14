import sys
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
FEISHU_OPS = PROJECT_ROOT / "feishu_ops"
if str(FEISHU_OPS) not in sys.path:
    sys.path.insert(0, str(FEISHU_OPS))


class ResourceRequestParserTests(unittest.TestCase):
    def tearDown(self):
        sys.modules.pop("resource_request_parser", None)

    def test_detects_apply_command(self):
        from resource_request_parser import is_resource_request

        self.assertTrue(is_resource_request("/apply\nLinux账号：zhangsan"))
        self.assertTrue(is_resource_request("申请资源 K100 4卡"))
        self.assertFalse(is_resource_request("我登录不上节点"))

    def test_parses_complete_apply_message(self):
        from resource_request_parser import parse_resource_request

        result = parse_resource_request("""
/apply
Linux账号：zhangsan
资源类型：K100
数量：4卡
使用时长：72小时
紧急程度：P1
项目：客户验收
用途：精度测试
截止时间：明天下午6点
是否接受排队：是
是否接受降配：否
""")

        self.assertTrue(result.valid)
        self.assertEqual(result.request.linux_username, "zhangsan")
        self.assertEqual(result.request.resource_type, "K100")
        self.assertEqual(result.request.resource_amount, 4)
        self.assertEqual(result.request.duration_hours, 72)
        self.assertEqual(result.request.urgency, "P1")
        self.assertEqual(result.request.project_name, "客户验收")
        self.assertEqual(result.request.reason, "精度测试")
        self.assertTrue(result.request.accept_queue)
        self.assertFalse(result.request.accept_downgrade)

    def test_reports_missing_fields(self):
        from resource_request_parser import parse_resource_request

        result = parse_resource_request("/apply\n资源类型：K100")

        self.assertFalse(result.valid)
        self.assertIn("linux_username", result.missing_fields)
        self.assertIn("resource_amount", result.missing_fields)
        self.assertIn("duration_hours", result.missing_fields)
        self.assertIn("urgency", result.missing_fields)
        self.assertIn("project_name_or_reason", result.missing_fields)


if __name__ == "__main__":
    unittest.main()
