# 容器问题 FAQ

## Q1: Docker 容器无法启动，提示网络代理错误

**问题描述**: 启动容器时失败，错误信息包含 proxy、connection refused 等关键词。

**可能原因**:
- 系统配置了 HTTP 代理，容器未继承代理设置
- 代理地址配置错误或代理服务不可达
- 容器内需要访问外部网络但被代理阻断

**解决方案**:
1. 检查 Docker daemon 代理配置：
   ```bash
   systemctl show docker --property=Environment
   ```
2. 如需配置代理，创建或编辑 `/etc/systemd/system/docker.service.d/http-proxy.conf`
3. 重启 Docker 服务使配置生效：
   ```bash
   systemctl daemon-reload
   systemctl restart docker
   ```
4. 如临时测试，可在运行时指定代理：
   ```bash
   docker run -e HTTP_PROXY=http://proxy:8080 <image>
   ```

---

## Q2: 容器内无法解析 DNS

**问题描述**: 容器内 ping 域名失败，提示 "Temporary failure in name resolution"。

**可能原因**:
- Docker DNS 配置错误
- 宿主机防火墙阻断 DNS 请求
- 容器网络模式配置不当

**解决方案**:
1. 检查 Docker daemon 的 DNS 配置（通常为 8.8.8.8 或宿主机 DNS）
2. 确认宿主机 DNS 解析正常工作
3. 启动容器时指定 DNS：
   ```bash
   docker run --dns 8.8.8.8 <image>
   ```
4. 检查容器网络模式，尝试使用 `--network=host`

---

## Q3: 容器存储卷挂载失败

**问题描述**: 挂载本地目录到容器时失败，提示权限拒绝或路径不存在。

**可能原因**:
- 挂载路径在宿主机上不存在
- SELinux 或 AppArmor 安全策略阻止
- 挂载路径权限不足

**解决方案**:
1. 确认宿主机挂载路径存在且路径正确
2. 检查路径权限，确保 Docker 进程有权限访问
3. 如使用 SELinux，添加 `:z` 或 `:rz` 标签：
   ```bash
   docker run -v /data:/data:z <image>
   ```
4. 确认目标目录不为空且权限正确

---

## Q4: 容器镜像拉取失败

**问题描述**: `docker pull` 或 `docker run` 时拉取镜像失败，提示 404 或超时。

**可能原因**:
- 镜像名称拼写错误或镜像不存在
- 私有仓库认证失败
- 网络问题导致拉取超时

**解决方案**:
1. 确认镜像名称正确，包括 registry 地址
2. 检查是否需要登录私有仓库：
   ```bash
   docker login <registry-url>
   ```
3. 配置镜像加速器（如使用国内镜像源）
4. 检查网络连通性，尝试 `ping` 仓库地址

---

## Q5: 容器内运行 CUDA 程序报错 "CUDA not found"

**问题描述**: 容器内运行 GPU 应用时报错找不到 CUDA。

**可能原因**:
- 未使用 NVIDIA Docker 运行时（nvidia-docker2）
- 容器未继承 GPU 设备挂载
- CUDA 库未正确打包进镜像

**解决方案**:
1. 确认已安装 nvidia-docker2：
   ```bash
   nvidia-docker version
   ```
2. 使用 nvidia 运行时启动容器：
   ```bash
   docker run --runtime=nvidia <image>
   ```
3. 确认镜像内 CUDA 库路径正确，设置 LD_LIBRARY_PATH
