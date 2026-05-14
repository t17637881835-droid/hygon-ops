"""飞书事件订阅签名验证"""
import hashlib
import hmac
import time
import base64

# 签名有效期窗口（秒）
SIGNATURE_VALIDITY_WINDOW = 60


def verify_feishu_signature(
    encrypt_key: str,
    timestamp: str,
    signature: str,
    body: bytes
) -> bool:
    """
    验证飞书事件订阅签名
    https://open.feishu.cn/document/ukTMukTMukTM/ucTM5YjL3ETO24yNxkjN
    """
    if not encrypt_key:
        # 未配置加密密钥时跳过验证（仅开发模式）
        return True

    try:
        # 检查时间戳有效性
        ts = int(timestamp)
        current = int(time.time())
        if abs(current - ts) > SIGNATURE_VALIDITY_WINDOW:
            return False

        # 构造签名内容: timestamp + "\n" + body
        sign_content = f"{timestamp}\n{body.decode('utf-8')}"

        # 使用 encrypt_key 作为密钥进行 HMAC-SHA256 签名
        key = encrypt_key.encode('utf-8')
        sign = hmac.new(key, sign_content.encode('utf-8'), hashlib.sha256).digest()

        # Base64 编码
        calculated = base64.b64encode(sign).decode('utf-8')

        return hmac.compare_digest(calculated, signature)
    except Exception:
        return False


def verify_verification_token(expected_token: str, payload: dict) -> bool:
    if not expected_token:
        return True
    token = payload.get("token") or payload.get("header", {}).get("token", "")
    return hmac.compare_digest(str(token), expected_token)


async def verify_request(request, config) -> bool:
    """验证飞书请求签名"""
    timestamp = request.headers.get("X-Lark-Timestamp", "")
    signature = request.headers.get("X-Lark-Signature", "")

    body = await request.body()

    return verify_feishu_signature(
        config.feishu.encrypt_key,
        timestamp,
        signature,
        body
    )
