# Resource Request System Design

## 1. Background

The `haiguang-ops` project is a Feishu-based operations assistant. It already supports private-message auto-reply, owner notification, manual owner intervention, pending message persistence, audit logs, and basic metrics.

This design extends the project with a reliable resource request and authorization workflow for GPU/DCU resources. The target operator is an operations administrator who receives resource requests through Feishu private messages and needs to allocate resources efficiently and safely.

The confirmed authorization model is:

```text
request -> resource pool -> target nodes -> node-local sshuser add/del -> timed revocation
```

Users already have Linux accounts. The system does not create user identities in the first production design. Granting access means running `/public/bin/sshuser add <username>` on each node mapped to the approved resource pool. Revoking access means running `/public/bin/sshuser del <username>` on those same nodes after the approved duration, with safeguards to avoid deleting pre-existing access.

## 2. Goals

- Support resource requests through Feishu private chat with the bot.
- Convert informal resource requests into structured records.
- Match each request to a configured resource pool.
- Use Prometheus as a read-only source for resource pool status.
- Help the owner approve, reject, defer, or inspect requests from Feishu.
- Generate a node-local `sshuser` authorization plan after approval.
- Support safe, confirmed `sshuser` grant and timed revocation in a later phase.
- Preserve full auditability for request, approval, grant, revoke, and failure events.
- Keep ordinary private-message auto-reply and resource-request workflow separated.

## 3. Non-goals

- Do not implement full automatic approval in the first version.
- Do not allow users to directly choose arbitrary nodes or shell commands.
- Do not allow Feishu messages to trigger arbitrary shell commands.
- Do not create or delete Linux users in the first version.
- Do not make Prometheus the only source of truth for approval decisions.
- Do not grant permanent resource access.
- Do not replace the existing private-message FAQ auto-reply workflow.

## 4. Existing System Context

The current project already has these relevant components:

- `feishu_ops/main.py`: FastAPI webhook entrypoint.
- `feishu_ops/feishu_event_parser.py`: Feishu message parsing and owner command recognition.
- `feishu_ops/message_queue.py`: pending private-message queue with SQLite persistence and `short_id` references.
- `feishu_ops/owner_notifier.py`: owner notification for messages requiring intervention.
- `feishu_ops/skill_invoker.py`: knowledge base and LLM answer generation.
- `feishu_ops/audit_logger.py`: JSONL audit logging.
- `feishu_ops/metrics_collector.py`: Prometheus-style application metrics.
- `feishu_ops/config.py`: environment-based config loading.

The resource request workflow should reuse the Feishu webhook, sender, audit, metrics, and scheduler infrastructure, but it should use separate request/grant storage rather than mixing resource requests into `MessageQueue`.

## 5. High-level Architecture

```text
Feishu Webhook
  |
  v
Event Parser
  |
  v
Intent Router
  |-- owner command
  |     |-- normal owner reply commands
  |     |-- resource approval commands
  |
  |-- resource request
  |     v
  |   ResourceRequestParser
  |     v
  |   ResourceRequestStore
  |     v
  |   ResourcePoolService
  |     v
  |   PrometheusResourceClient
  |     v
  |   PriorityScorer
  |     v
  |   ResourceApprovalNotifier
  |     v
  |   GrantPlanner
  |     v
  |   SshuserGrantService
  |     v
  |   GrantReaper
  |
  |-- ordinary operations question
        v
      MessageQueue / OwnerNotifier / SkillInvoker
```

Routing priority:

```text
owner command > resource request command > ordinary operations Q&A
```

## 6. Proposed New Modules

### 6.1 `resource_request_parser.py`

Responsibility:

- Detect resource request intent.
- Parse `/apply` request text.
- Extract structured fields.
- Return missing fields when the request is incomplete.

Supported initial user inputs:

```text
/apply
申请资源
我要申请资源
我要 K100
需要 4 卡
```

Recommended first complete format:

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

Required fields for MVP:

- Linux account
- Resource type
- Resource amount
- Duration
- Urgency
- Project name or reason

Optional fields:

- Deadline
- Accept queue
- Accept downgrade
- Extra notes

### 6.2 `resource_request_store.py`

Responsibility:

- Store resource requests in SQLite.
- Store grant plans and grant results.
- Enforce request and grant state transitions.
- Query pending requests.
- Query requests by code.
- Query active grants for expiration checks.

### 6.3 `resource_config.py`

Responsibility:

- Load resource pool config from `config/resource_pools.yml`.
- Validate each pool.
- Build a node whitelist from the configured pools.
- Validate default and maximum grant durations.

### 6.4 `resource_pool.py`

Responsibility:

- Match a request to a resource pool.
- Hide node selection from users.
- Return pool metadata for approval notification.
- Prevent disabled pools from being selected.

Matching inputs:

- `resource_type`
- `resource_amount`
- `urgency`
- pool availability from Prometheus
- optional policy rules

### 6.5 `resource_prometheus.py`

Responsibility:

- Query Prometheus in read-only mode.
- Convert real Prometheus metrics into a stable internal structure.
- Return resource pool status for owner decision-making.
- Degrade gracefully when Prometheus is unavailable.

Internal status model:

```text
pool_id
total_devices
free_devices
used_devices
avg_utilization
healthy_nodes
unhealthy_nodes
collected_at
source
raw
```

### 6.6 `resource_priority.py`

Responsibility:

- Calculate a transparent priority score.
- Return both numeric score and human-readable reasons.

Initial scoring model:

```text
P0 = +100
P1 = +70
P2 = +40
P3 = +10

deadline < 12h = +50
deadline < 24h = +40
deadline < 72h = +20

production incident = +50
customer delivery / acceptance = +30
internal test = +10

pool can satisfy request = +10
pool is tight = -20
accept queue = +5
accept downgrade = +5
```

### 6.7 `resource_approval.py`

Responsibility:

- Parse owner resource commands.
- Validate owner permissions.
- Apply state transitions.
- Send confirmations to owner and requester.

Owner commands:

```text
/queue
/detail R12
/approve R12 72h
/reject R12 reason
/defer R12 4h
/grant R12 confirm
/revoke R12
/pool
/pool k100_train
```

### 6.8 `sshuser_grant_service.py`

Responsibility:

- Query whether a Linux user currently has node login access when supported.
- Execute or advise `/public/bin/sshuser add <username>` on whitelisted nodes.
- Execute or advise `/public/bin/sshuser del <username>` on whitelisted nodes.
- Track whether access existed before the grant to prevent timed revocation from deleting unrelated long-term access.

Hard restrictions:

- No arbitrary node operations.
- No arbitrary command paths from Feishu input.
- No shell command execution from Feishu input.
- No user creation or deletion in MVP.
- No password changes.

### 6.9 `grant_reaper.py`

Responsibility:

- Periodically check active grants.
- Revoke grants after `valid_until`.
- Send expiration reminders.
- Retry failed revocations.
- Notify owner on failure.
- Record audit logs.

## 7. Resource Pool Configuration

Add:

```text
config/resource_pools.yml
```

Example:

```yaml
resource_pools:
  - pool_id: k100_train
    name: K100-训练池
    description: 通用 K100 训练资源池
    resource_type: K100
    nodes:
      - node01
      - node02
      - node03
      - node04
    sshuser_path: /public/bin/sshuser
    total_devices: 32
    default_grant_hours: 72
    max_grant_hours: 168
    min_free_devices_for_auto_suggest: 4
    enabled: true
    prometheus:
      labels:
        pool: k100_train
        accelerator: k100

  - pool_id: z100_infer
    name: Z100-推理池
    description: Z100 推理测试资源池
    resource_type: Z100
    nodes:
      - node11
      - node12
    sshuser_path: /public/bin/sshuser
    total_devices: 16
    default_grant_hours: 24
    max_grant_hours: 72
    min_free_devices_for_auto_suggest: 2
    enabled: true
    prometheus:
      labels:
        pool: z100_infer
        accelerator: z100
```

Validation rules:

- `pool_id` must be unique.
- `nodes` must not be empty for pools that expose SSH access.
- `sshuser_path` must be configured or default to `/public/bin/sshuser`.
- One resource pool maps to one node list.
- Disabled pools cannot receive new grants.
- `max_grant_hours` must be positive.
- `default_grant_hours` must not exceed `max_grant_hours`.

## 8. SQLite Schema

### 8.1 `resource_requests`

```text
id integer primary key
request_code text unique
feishu_user_id text
linux_username text
project_name text
resource_type text
resource_amount integer
duration_hours integer
urgency text
deadline text
reason text
accept_queue integer
accept_downgrade integer
matched_pool_id text
priority_score integer
priority_reasons text
status text
created_at text
updated_at text
approved_by text
approved_at text
reject_reason text
```

### 8.2 `resource_grants`

```text
id integer primary key
grant_code text unique
request_code text
linux_username text
pool_id text
target_nodes text
sshuser_path text
valid_from text
valid_until text
status text
planned_by text
confirmed_by text
granted_at text
revoked_at text
last_error text
created_at text
updated_at text
```

### 8.3 `resource_audit_logs`

```text
id integer primary key
event text
request_code text
grant_code text
actor_feishu_id text
linux_username text
pool_id text
target_nodes text
details text
created_at text
```

### 8.4 `resource_pool_snapshots`

Optional for MVP, useful for later analysis:

```text
id integer primary key
pool_id text
total_devices integer
free_devices integer
used_devices integer
avg_utilization real
healthy_nodes integer
unhealthy_nodes integer
raw_metrics text
created_at text
```

## 9. State Machines

### 9.1 Request states

Main path:

```text
pending -> approved -> planned -> granted -> expired
```

Other paths:

```text
pending -> rejected
pending -> cancelled
approved -> rejected
planned -> failed
granted -> revoked
granted -> revoke_failed
granted -> expired
```

Recommended transition restrictions:

- Only `pending` can be approved or rejected.
- Only `approved` can produce a grant plan.
- Only `planned` can be granted.
- Only `granted` can be revoked or expired.
- Re-running grant/revoke should be idempotent.

### 9.2 Grant states

```text
planned -> granted -> revoked
planned -> failed
granted -> revoke_failed -> revoked
granted -> expired
```

## 10. User Flows

### 10.1 User submits request

User sends:

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
```

Bot replies:

```text
✅ 资源申请已提交：R12
状态：待审批
管理员会根据资源池状态和任务紧急程度处理。
```

If fields are missing:

```text
请补充以下信息：
1. Linux账号
2. 使用时长
3. 项目/用途
```

### 10.2 Owner receives approval notification

```text
📦 资源申请 R12

申请人：zhangsan
项目：客户验收
申请资源：4 × K100
使用时长：72h
紧急程度：P1
优先级评分：115

评分原因：
- P1: +70
- 截止时间 < 24h: +40
- 客户验收: +30
- 资源池可满足: +10

推荐资源池：K100-训练池
目标节点：node01,node02,node03,node04

资源池状态：
总卡数：32
空闲卡：6
平均利用率：62%
异常节点：1

建议：当前资源池可以满足申请。

操作：
/approve R12 72h
/reject R12 原因
/defer R12 4h
/detail R12
```

### 10.3 Owner approves

Owner sends:

```text
/approve R12 72h
```

Phase 1 response:

```text
✅ 已批准 R12

授权建议：
在目标节点执行 /public/bin/sshuser add zhangsan
有效期：72h
到期时间：2026-05-14 10:00

当前阶段不会自动执行节点命令，请人工执行授权。
```

Phase 2 response:

```text
✅ 已批准 R12，已生成授权计划。

将执行：
1. 在 node01,node02,node03,node04 执行 /public/bin/sshuser add zhangsan
2. 有效期 72h
3. 到期在相同节点执行 /public/bin/sshuser del zhangsan

确认执行：
/grant R12 confirm
```

### 10.4 Owner confirms grant

Owner sends:

```text
/grant R12 confirm
```

Bot executes node-local `sshuser add` and replies:

```text
✅ 授权完成：申请 R12 已通过
用户：zhangsan
资源池：K100-训练池
目标节点：node01,node02,node03,node04
有效期至：2026-05-14 10:00
```

Requester receives:

```text
✅ 资源申请 R12 已通过

资源池：K100-训练池
Linux账号：zhangsan
有效期：2026-05-11 10:00 至 2026-05-14 10:00

你可以使用 SSH 登录该资源池节点。
节点列表：
node01
node02
node03
node04

到期后系统会自动回收访问权限。
```

### 10.5 Grant expiration

Before expiration:

```text
⏰ 资源申请 R12 将在 2 小时后到期。
如需延期，请重新提交申请或联系管理员。
```

After successful revocation:

```text
✅ 资源申请 R12 已到期，访问权限已回收。
```

If revocation fails:

```text
🚨 权限回收失败：R12
用户：zhangsan
目标节点：node02
错误：sshuser command timeout
请人工处理或稍后重试 /revoke R12。
```

## 11. Prometheus Integration

Prometheus is a read-only decision support source.

The business layer should not depend on raw metric names. It should depend on an internal normalized status model.

Initial internal metrics:

```text
total_devices
free_devices
used_devices
avg_utilization
healthy_nodes
unhealthy_nodes
collected_at
```

If Prometheus is unavailable:

- The request can still be submitted.
- The owner notification should show that resource status is unavailable.
- The owner can still approve manually.
- The audit log should record `prometheus_unavailable` or equivalent detail.

## 12. SSHUser Safety Model

### 12.1 Whitelist

Allowed nodes are derived from `resource_pools.yml`.

No operation may target a node outside this whitelist.

### 12.2 Confirmation required

Real node-local authorization requires two steps:

```text
/approve R12 72h
/grant R12 confirm
```

### 12.3 Idempotency

Grant behavior:

- If the user already has login access on a node, treat grant as successful.
- Record that access was already present before this resource grant.

Revoke behavior:

- If this system did not create the node access, do not delete it.
- If another active grant still needs the same user-node access, do not delete it.
- If the user is already absent from the node allow list, treat revoke as successful.

### 12.4 Minimal permissions

The remote execution identity may only:

- Connect to configured resource-pool nodes.
- Execute the configured `sshuser_path` with `add <username>` or `del <username>`.
- Query node access state when supported.

It must not:

- Create users.
- Delete users.
- Modify passwords.
- Execute arbitrary shell commands from Feishu input.
- Modify nodes outside configured resource pools.

### 12.5 Audit required

Every `sshuser` grant or revoke attempt must record:

- request code
- grant code
- actor
- Linux username
- pool ID
- target node
- operation type
- result
- error message if any
- timestamp

## 13. Configuration

Add environment variables:

```env
RESOURCE_REQUEST_ENABLED=true
RESOURCE_POOLS_CONFIG_PATH=./config/resource_pools.yml
PROMETHEUS_URL=http://prometheus:9090
PROMETHEUS_TIMEOUT_SECONDS=5

SSHUSER_GRANT_ENABLED=false
SSHUSER_COMMAND_PATH=/public/bin/sshuser
SSHUSER_REMOTE_EXEC_ENABLED=false
SSHUSER_CONNECT_TIMEOUT_SECONDS=5

RESOURCE_DEFAULT_GRANT_HOURS=24
RESOURCE_MAX_GRANT_HOURS=168
RESOURCE_GRANT_CONFIRM_REQUIRED=true
RESOURCE_EXPIRE_CHECK_INTERVAL_MINUTES=5
RESOURCE_EXPIRE_REMIND_HOURS=2
```

Default for MVP:

```text
SSHUSER_GRANT_ENABLED=false
SSHUSER_REMOTE_EXEC_ENABLED=false
```

This prevents accidental production node mutation before the flow is validated.

## 14. Phased Delivery Plan

### Phase 0: Service hardening

- Add systemd deployment option.
- Keep Docker as optional deployment path.
- Enhance config checks.
- Enhance `/health`.
- Add deployment and operations docs.
- Ensure tests pass reliably.

### Phase 1: Resource request MVP without node mutation

- Add resource pool config.
- Add `/apply` parsing.
- Store requests in SQLite.
- Match resource pool.
- Calculate priority score.
- Query or mock Prometheus status.
- Notify owner for approval.
- Support `/queue`, `/detail`, `/approve`, `/reject`, `/defer`, `/pool`.
- Generate `sshuser` authorization advice only.
- Record audit logs.

### Phase 2: Confirmed `sshuser` grant and revoke

- Add `sshuser_grant_service.py`.
- Add `/grant R12 confirm`.
- Enforce node whitelist.
- Add idempotent grant/revoke.
- Add timed revocation through `grant_reaper.py`.
- Add owner notification for grant/revoke failures.
- Add integration tests with fake node execution.

### Phase 3: Limited automatic approval

Only consider after Phase 2 is stable.

Possible auto-approval constraints:

```text
urgency is P3 or lower
requested amount <= 1 card
duration <= 8 hours
resource pool free ratio > 60%
user has good history
sshuser grant enabled and healthy
```

## 15. Testing Strategy

### Unit tests

- Resource request parser extracts valid fields.
- Parser reports missing required fields.
- Resource pool config validates unique pool IDs and non-empty node lists.
- Pool matcher selects the correct pool.
- Priority scorer returns expected score and reasons.
- Approval command parser handles valid and invalid commands.
- Store enforces request and grant state transitions.
- `sshuser` service rejects non-whitelisted nodes.
- Grant/revoke are idempotent.

### Integration tests

- Feishu payload for `/apply` creates a pending resource request.
- Owner `/approve` generates a grant plan.
- Owner `/reject` notifies requester.
- Prometheus unavailable path still allows manual approval.
- `/grant R12 confirm` works with fake node execution.
- Expired grant triggers fake `sshuser del`.
- Revoke failure notifies owner.

### Acceptance tests

- A user can submit a valid resource request through private chat.
- Owner receives a clear approval notification.
- Owner can inspect pending requests.
- Owner can approve or reject.
- Phase 1 never mutates node access.
- Phase 2 mutates only whitelisted nodes.
- Grant expiration revokes access or alerts owner on failure.
- Every important action is auditable.

## 16. Risks and Mitigations

### Risk: user provides wrong Linux username

Mitigation:

- Validate Linux username format before approval.
- Optionally add a Feishu ID to Linux username binding table later.
- Ask owner to confirm first-time mappings.

### Risk: wrong pool authorization

Mitigation:

- Users cannot input target nodes or shell commands.
- Pools are selected from config.
- Owner notification displays pool and target nodes.
- Real node mutation requires confirmation.

### Risk: remote execution permissions too broad

Mitigation:

- Use a least-privilege remote execution identity.
- Enforce configured node whitelist in application code.
- Never pass Feishu text as a shell command.
- Audit all attempts.

### Risk: timed revocation deletes pre-existing access

Mitigation:

- Record whether access existed before a grant.
- Do not run `sshuser del` for access that predated the grant.
- Do not run `sshuser del` while another active grant still references the same user and node.

### Risk: failed revocation

Mitigation:

- Retry revocation.
- Notify owner immediately.
- Expose `/revoke R12` manual command.
- Record failure in audit and grant state.

### Risk: Prometheus data missing or stale

Mitigation:

- Treat Prometheus as advisory.
- Include `collected_at` in owner notification.
- Allow manual approval when metrics are unavailable.
- Record metrics failures in audit.

## 17. Open Decisions Before Implementation

These should be resolved before Phase 2 `sshuser` mutation:

- Exact remote execution mechanism and service account.
- Whether `sshuser` can query existing AllowUsers state before mutation.
- How to persist pre-existing access markers per user and node.
- How to handle partial node grant or revoke failure.
- Whether active SSH sessions should be terminated on expiration.
- Exact Prometheus metric names for DCU/GPU pool status.

These do not block Phase 1 because Phase 1 does not mutate node access.

## 18. Recommended Next Step

Proceed with an implementation plan for Phase 0 and Phase 1 first.

Recommended initial implementation scope:

```text
1. Add resource pool config loader and sample config.
2. Add resource request parser.
3. Add SQLite store for requests and grant plans.
4. Add priority scorer.
5. Add Prometheus client with graceful unavailable handling.
6. Add owner approval commands without node mutation.
7. Add tests and docs.
```

`sshuser` mutation should remain disabled until the Phase 1 workflow is validated end-to-end.
