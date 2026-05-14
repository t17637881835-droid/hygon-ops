# 海光 DCU 运维值守助手

这是一个接入飞书的运维问答小助手，用于在你长时间未回复时，根据本地知识库自动回答常见运维问题；低置信度时返回忙碌兜底话术，避免胡乱回答。

## 当前能力

- 接收飞书消息事件
- 识别人工回复并取消待处理消息
- SQLite 持久化待回复队列
- 本地知识库检索
- 低置信度兜底回复
- 飞书 Bot API 按 `chat_id` 回复原会话
- Webhook 降级发送
- 发送失败重试
- JSONL 审计日志
- 健康检查与 metrics 接口
- 私聊资源申请 `/apply` 工作流
- 资源池节点列表配置校验
- Prometheus 只读资源池状态查询
- 运维审批命令 `/approve`、`/reject`
- Phase 1 `sshuser` 授权建议生成，不自动执行节点命令
- Phase 2 owner 确认后通过跳板机执行节点本地 `sshuser` 授权与到期撤权

## 目录结构

```text
feishu_ops/                 # 服务代码
skills/haiguang-ops/        # skill 与知识库
docker/                     # Docker 部署文件
tests/                      # 回归测试
.env.example                # 环境变量模板
```

## 环境变量

复制模板：

```powershell
Copy-Item .env.example .env
```

至少需要配置：

```env
FEISHU_APP_ID=cli_xxx
FEISHU_APP_SECRET=xxx
FEISHU_OWNER_USER_IDS=ou_xxx
ANTHROPIC_API_KEY=sk-ant-xxx
```

建议配置：

```env
MESSAGE_QUEUE_DB_PATH=/app/data/message_queue.db
RESOURCE_REQUEST_DB_PATH=/app/data/resource_requests.db
AUDIT_LOG_PATH=/app/logs/audit.jsonl
KNOWLEDGE_BASE_PATH=/app/skills/haiguang-ops/knowledge_base
```

资源申请 MVP 可选配置：

```env
RESOURCE_REQUEST_ENABLED=true
RESOURCE_POOLS_CONFIG_PATH=/app/config/resource_pools.yml
RESOURCE_REQUEST_DB_PATH=/app/data/resource_requests.db
PROMETHEUS_URL=http://prometheus:9090
PROMETHEUS_TIMEOUT_SECONDS=5
SSHUSER_GRANT_ENABLED=false
SSHUSER_COMMAND_PATH=/public/bin/sshuser
SSHUSER_REMOTE_EXEC_ENABLED=false
SSHUSER_JUMP_HOST=
SSHUSER_JUMP_PORT=22
SSHUSER_JUMP_USER=resource_bot
SSHUSER_SSH_KEY_PATH=/app/secrets/resource_bot_id_rsa
SSHUSER_KNOWN_HOSTS_PATH=/app/secrets/known_hosts
SSHUSER_TARGET_USER=resource_exec
SSHUSER_TARGET_SSH_PORT=22
SSHUSER_CONNECT_TIMEOUT_SECONDS=5
SSHUSER_COMMAND_TIMEOUT_SECONDS=15
SSHUSER_MAX_RETRIES=2
SSHUSER_RETRY_BACKOFF_SECONDS=3
SSHUSER_MAX_PARALLEL_NODES=1
SSHUSER_EXECUTOR_TYPE=jump_host
```

## 飞书开放平台配置

1. 创建企业自建应用。
2. 开通机器人能力。
3. 配置事件订阅地址：

```text
https://你的公网域名/webhook
```

4. 订阅事件：

```text
im.message.receive_v1
```

5. 记录以下信息并写入 `.env`：

```text
App ID
App Secret
Verification Token（如飞书事件订阅页面提供）
Encrypt Key（如启用签名/加密）
你的 user_id（填入 FEISHU_OWNER_USER_IDS）
机器人 user_id（填入 FEISHU_BOT_USER_IDS）
```

## 本地配置自检

```powershell
python feishu_ops/config_check.py
```

容器内自检：

```bash
python feishu_ops/config_check.py
```

## Docker 启动

在项目根目录执行：

```powershell
docker compose -f docker/docker-compose.yml --env-file .env up -d --build
```

查看日志：

```powershell
docker compose -f docker/docker-compose.yml logs -f
```

停止服务：

```powershell
docker compose -f docker/docker-compose.yml down
```

## 接口

健康检查：

```text
GET /health
```

指标：

```text
GET /metrics
```

飞书事件入口：

```text
POST /webhook
```

## 资源申请 MVP

用户在私聊中发送：

```text
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
```

系统会：

- 解析结构化申请字段
- 按资源类型和数量匹配资源池
- 查询 Prometheus 获取资源池空闲状态，查询失败时降级为 `unknown`
- 计算透明优先级评分和评分原因
- 写入 SQLite 申请表
- 私聊通知用户已受理
- 私聊通知运维 owner 审批命令

运维 owner 可在私聊中发送：

```text
/approve R1 72h
/reject R1 资源不足
```

资源池配置中的 `nodes` 是授权目标节点。现网登录权限由每台节点本地的 `/public/bin/sshuser` 命令维护，该命令会修改节点 `/etc/ssh/sshd_config` 的 `AllowUsers`。

Phase 1 默认 `SSHUSER_GRANT_ENABLED=false`，审批后只生成授权建议，例如：

```text
node01: /public/bin/sshuser add zhangsan
node02: /public/bin/sshuser add zhangsan

到期撤权：
node01: /public/bin/sshuser del zhangsan
node02: /public/bin/sshuser del zhangsan
```

开启 Phase 2 需要同时设置 `SSHUSER_GRANT_ENABLED=true` 与 `SSHUSER_REMOTE_EXEC_ENABLED=true`，并配置跳板机、SSH key、known_hosts 和目标节点执行用户。`/approve` 仍只创建授权计划，不会立即改节点；owner 需要二次确认：

```text
/grant G1 confirm
/grant G1 retry
/revoke G1 retry
/revoke G1 mark-done node01,node02
```

Phase 2 会按节点记录授权/撤权结果，并保护既有登录权限和其他仍活跃的系统 grant，避免到期撤权误删。

## 数据文件

默认 Docker 挂载：

```text
docker/data/message_queue.db   # 待回复消息队列
docker/data/resource_requests.db # 资源申请与授权计划
docker/logs/audit.jsonl        # 自动回复审计日志
docker/logs/app_*.log          # 应用运行日志
```

## 知识库维护

本地知识库路径：

```text
skills/haiguang-ops/knowledge_base/
```

核心 FAQ 文件：

```text
skills/haiguang-ops/knowledge_base/faq.json
```

建议每条 FAQ 至少包含：

```json
{
  "id": "gpu-driver-install",
  "category": "gpu",
  "question": "驱动怎么安装",
  "keywords": ["驱动", "安装", "GPU"],
  "solution": "..."
}
```

## 部署

### 首次部署

```bash
# 在项目根目录执行，secrets 统一在根目录 .env 里
cd /public/home/tianly/haiguang-ops-LL
docker compose -f docker/docker-compose.yml --env-file .env up -d --build
```

### 更新代码后重新部署

```bash
cd /public/home/tianly/haiguang-ops-LL
docker compose -f docker/docker-compose.yml --env-file .env up -d --build
```

### 查看日志

```bash
docker logs docker-haiguang-ops-1 -f --tail 50
```

### 停止服务

```bash
cd /public/home/tianly/haiguang-ops-LL
docker compose -f docker/docker-compose.yml down
```

> **注意**：必须加 `--env-file .env`，否则 Docker Compose 默认读 `docker/.env`，导致 `FEISHU_APP_SECRET`、`ANTHROPIC_API_KEY` 等 secrets 无法传入容器，飞书长连接会失败。

---

## 运行测试

```powershell
python -m unittest discover -s tests -v
python -m compileall feishu_ops tests
```

## 注意事项

- `RAGFlowRetriever` 当前是安全占位实现，未真正调用 RAGFlow API。
- 如果未设置 `FEISHU_OWNER_USER_IDS`，系统无法判断你是否已人工回复。
- 如果飞书 Bot API 发送失败，系统会降级到 Webhook；Webhook 只能发到固定群。
- 当前适合单实例部署；多实例部署需要分布式锁避免重复自动回复。
- Phase 2 远程执行会通过跳板机运行节点本地 `/public/bin/sshuser`；生产开启前请确认跳板机账号、目标执行用户、sudo 权限和 known_hosts 均已按最小权限配置。
