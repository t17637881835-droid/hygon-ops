# 集群登录运维文档

## 登录流程概述

海光 DCU 集群的登录流程通常分为以下几个步骤：

```
本地终端 -> 跳板机/跳转节点 -> 集群控制节点 -> 计算节点
```

## 登录方式

### 方式一：SSH 公钥登录（推荐）

1. 生成 SSH 密钥对（如果尚未生成）：
   ```bash
   ssh-keygen -t rsa -b 4096 -C "your_email@company.com"
   ```

2. 将公钥复制到跳转节点：
   ```bash
   ssh-copy-id -i ~/.ssh/id_rsa.pub user@jump-server
   ```

3. 配置 SSH 客户端（~/.ssh/config）：
   ```
   Host jump
       HostName jump-server.company.com
       User your_username
       Port 22
       IdentityFile ~/.ssh/id_rsa

   Host cluster
       HostName cluster-control-node
       User your_username
       ProxyJump jump
       IdentityFile ~/.ssh/id_rsa
   ```

4. 登录跳转节点：
   ```bash
   ssh jump
   ```

5. 从跳转节点登录集群：
   ```bash
   ssh cluster
   ```

### 方式二：动态验证码登录

1. 使用手机上的动态令牌应用获取验证码
2. 验证码通常在 30 秒内有效
3. 输入格式：`password` + `动态码` 或单独动态码（根据系统配置）

## 节点类型说明

| 节点类型 | 说明 | 访问方式 |
|---------|------|---------|
| 跳转节点 | 外部访问集群的唯一入口 | SSH 公钥或动态码 |
| 控制节点 | 集群管理节点，运行 K8s/DCU 控制平面 | 通过跳转节点二次登录 |
| 计算节点 | 运行 DCU 训练/推理任务 | 通常通过作业调度系统提交 |
| 存储节点 | NFS/CIFS 存储访问节点 | 挂载使用 |

## 常见登录问题排查

### 问题1：SSH 连接超时

**检查项**：
- 确认网络可以访问互联网
- 确认 VPN 已连接（如需要）
- 检查目标节点是否在线：`ping jump-server`
- 确认端口正确（默认 22）

### 问题2：公钥登录失败

**检查项**：
- 确认公钥已正确添加到目标机器
- 检查本地私钥权限：`chmod 600 ~/.ssh/id_rsa`
- 检查 SSH 配置是否指定了正确的密钥文件
- 查看服务端日志：`journalctl -u sshd`

### 问题3：动态码失效

**解决措施**：
- 确保手机时间准确
- 等待新动态码生成后再试
- 如持续失败，联系管理员重新绑定令牌

## 安全注意事项

1. **不要将私钥文件明文存储在共享存储上**
2. **定期更换 SSH 密钥对**
3. **离开终端时使用 `exit` 或 `logout` 退出会话**
4. **不要在跳板机上进行与工作无关的操作**
5. **如发现异常登录记录，立即报告安全团队**

## 快速参考命令

```bash
# 登录跳转节点
ssh user@jump-server

# 登录集群控制节点
ssh user@cluster-control-node

# 查看当前登录状态
whoami
hostname
pwd

# 查看集群节点列表
kubectl get nodes

# 查看当前用户的作业
kubectl get pods -o wide

# SSH 配置测试
ssh -v user@jump-server
```
