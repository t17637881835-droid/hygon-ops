# 网络问题 FAQ

## Q1: VPN 连接成功但无法访问内网资源

**问题描述**: VPN 显示已连接，但无法访问集群节点或内网服务。

**可能原因**:
- VPN 路由配置不完整，未包含目标网段
- 本地防火墙阻止内网流量
- DNS 配置未切换到内网 DNS

**解决方案**:
1. 检查 VPN 连接属性，确认已启用"允许内网资源访问"选项
2. 添加内网路由表（联系网络管理员获取路由配置）
3. 检查本地防火墙规则，确保 UDP 1194 端口开放
4. 确认内网 DNS 配置，修改 `/etc/resolv.conf` 或使用 DHCP 获取

---

## Q2: SSH 连接空闲后自动断开

**问题描述**: SSH 会话在一段时间不操作后自动断开连接。

**可能原因**:
- SSH 服务端配置了 ClientAliveInterval
- 网络设备（路由器/交换机）有超时策略
- 防火墙有连接超时限制

**解决方案**:
1. 在 SSH 客户端配置文件中启用保活：
   ```bash
   # ~/.ssh/config
   Host *
       ServerAliveInterval 60
       ServerAliveCountMax 3
   ```
2. 使用 `screen` 或 `tmux` 保持会话
3. 联系网络管理员检查网络设备超时设置
4. 尝试使用 `-o ServerAliveInterval=60` 参数临时解决

---

## Q3: 内网 IP 地址冲突

**问题描述**: 同一网段内多个设备使用相同 IP 地址，导致网络不稳定。

**可能原因**:
- DHCP 范围与静态 IP 分配范围重叠
- 设备手动配置了冲突的静态 IP
- 网络配置错误导致 IP 复用

**解决方案**:
1. 使用 `arp -a` 或 `ip neigh show` 检查冲突设备 MAC 地址
2. 确认冲突 IP 不在 DHCP 范围内
3. 如使用静态 IP，改为使用 DHCP 或分配空闲 IP
4. 联系网络管理员协调 IP 分配

---

## Q4: 特定端口无法访问

**问题描述**: 服务已启动，但外部无法通过端口访问。

**可能原因**:
- 服务仅监听 localhost（127.0.0.1）而非 0.0.0.0
- 防火墙规则阻止了该端口
- 云安全组配置未开放对应端口

**解决方案**:
1. 检查服务监听地址，确认为 `0.0.0.0` 而非 `127.0.0.1`
2. 检查 iptables 规则：
   ```bash
   iptables -L -n | grep <port>
   ```
3. 开放防火墙端口：
   ```bash
   firewall-cmd --add-port=<port>/tcp
   ```
4. 如使用云平台，检查安全组规则是否放行该端口

---

## Q5: ping 不通外网

**问题描述**: 节点无法访问外网，ping 公网 IP 无响应。

**可能原因**:
- 网关配置错误
- DNS 解析失败
- 防火墙规则阻止出站流量
- 外网链路故障

**解决方案**:
1. 检查网关配置：`ip route show`
2. 测试 DNS：`nslookup baidu.com`
3. 检查防火墙：`iptables -L -n`
4. 尝试直连 IP：`ping 8.8.8.8`
5. 联系网络管理员检查外网链路

---

## Q6: NCCL 通信超时

**问题描述**: 分布式训练时 NCCL 报错通信超时。

**可能原因**:
- 网络带宽不足
- 节点间网络延迟高
- 防火墙阻止 NCCL 端口
- NCCL 环境变量配置不当

**解决方案**:
1. 检查节点间网络：`ibstat` 或 `ibv_devinfo`
2. 增加 NCCL 超时时间：`export NCCL_TIMEOUT=1800`
3. 指定 NCCL 通信后端：`export NCCL_IB_DISABLE=0`
4. 检查防火墙是否开放 NCCL 端口（默认 5555-65535）
5. 使用 `NCCL_DEBUG=INFO` 查看详细日志

---

## Q7: 网卡配置文件在哪

**问题描述**: 需要修改网卡 IP 配置但找不到配置文件。

**可能原因**:
- 不同发行版配置文件路径不同
- 使用 NetworkManager 而非传统配置文件

**解决方案**:
1. **CentOS/RHEL**: `/etc/sysconfig/network-scripts/ifcfg-eth0`
2. **Ubuntu**: `/etc/netplan/00-installer-config.yaml` 或 `/etc/network/interfaces`
3. **Kylin**: 同 CentOS 路径
4. 修改后重启网络服务：
   - CentOS: `systemctl restart network`
   - Ubuntu: `netplan apply` 或 `systemctl restart networking`
