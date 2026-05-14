# 存储问题 FAQ

## Q1: NFS 挂载失败

**问题描述**: 执行 mount 命令挂载 NFS 时失败，提示 "Connection refused" 或超时。

**可能原因**:
- NFS 服务未启动
- 网络不通或防火墙阻断
- NFS 导出路径配置错误
- 客户端未安装 nfs-utils

**解决方案**:
1. 确认 NFS 服务端状态：`systemctl status nfs-server`
2. 检查网络连通性：`ping <nfs-server-ip>`
3. 确认 NFS 导出配置：`exportfs -v`
4. 客户端安装 nfs-utils：
   ```bash
   yum install -y nfs-utils
   ```
5. 使用 showmount 检查导出：`showmount -e <nfs-server>`

---

## Q2: 磁盘空间不足

**问题描述**: 写入数据时提示 "No space left on device"。

**可能原因**:
- 挂载点所在分区已满
- inode 节点耗尽
- 存在大文件或日志文件未清理

**解决方案**:
1. 检查磁盘使用情况：
   ```bash
   df -h
   du -sh /*
   ```
2. 清理不需要的文件、日志和缓存
3. 清理 inode（如果有大量小文件）：
   ```bash
   df -i
   ```
4. 扩展磁盘空间（联系系统管理员）
5. 迁移数据到其他存储

---

## Q3: 挂载的 NFS 存储权限不足

**问题描述**: 对 NFS 挂载目录无写入权限。

**可能原因**:
- NFS 服务端导出配置缺少 `rw` 选项
- UID/GID 不匹配
- 挂载选项指定了 `ro`（只读）

**解决方案**:
1. 检查挂载选项是否为只读：`mount | grep nfs`
2. 确认服务端导出配置包含 `rw`：
   ```
   /data *(rw,sync,no_subtree_check)
   ```
3. 重新挂载并指定权限：
   ```bash
   mount -o remount,rw <mount-point>
   ```
4. 如 UID 不匹配，可使用 `uid` 和 `gid` 选项挂载：
   ```bash
   mount -o uid=1000,gid=1000 <nfs-server>:/data /mnt
   ```

---

## Q4: 文件权限导致无法访问

**问题描述**: 即使使用 root 用户也无法访问某些文件或目录。

**可能原因**:
- NFS 存储上的 ACL（访问控制列表）限制
- 文件系统本身权限问题
- SELinux 或 AppArmor 限制

**解决方案**:
1. 检查文件 ACL：
   ```bash
   getfacl <filepath>
   ```
2. 检查 SELinux 上下文：
   ```bash
   ls -Z <filepath>
   ```
3. 临时关闭 SELinux 测试：
   ```bash
   setenforce 0
   ```
4. 修复 ACL：
   ```bash
   setfacl -m u:username:rwx <filepath>
   ```

---

## Q5: 存储 I/O 延迟高

**问题描述**: 文件读写速度慢，I/O 操作延迟明显。

**可能原因**:
- NFS 网络延迟高
- 存储服务器负载高
- 磁盘硬件问题

**解决方案**:
1. 测试网络延迟：`ping <storage-server>`
2. 使用 iostat 检查 I/O 状态：
   ```bash
   iostat -x 1
   ```
3. 使用 dd 测试实际 I/O 性能：
   ```bash
   dd if=/dev/zero of=<test-file> bs=1M count=100
   ```
4. 联系存储管理员检查存储服务器状态
