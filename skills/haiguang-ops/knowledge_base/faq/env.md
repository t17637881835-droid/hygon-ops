# 环境变量问题 FAQ

## Q1: PATH 环境变量未包含 CUDA 路径

**问题描述**: 运行 nvcc 等 CUDA 命令时报错 "command not found"。

**可能原因**:
- CUDA 未正确安装或安装路径非标准位置
- .bashrc 或 .profile 中未配置 PATH
- 多个 CUDA 版本导致 PATH 指向错误版本

**解决方案**:
1. 确认 CUDA 安装路径：`ls /usr/local/cuda*/bin/nvcc`
2. 配置环境变量，在 `~/.bashrc` 或 `~/.profile` 中添加：
   ```bash
   export PATH=/usr/local/cuda/bin:$PATH
   ```
3. 使配置生效：
   ```bash
   source ~/.bashrc
   ```
4. 验证配置：`echo $PATH` 和 `which nvcc`

---

## Q2: LD_LIBRARY_PATH 未包含 CUDA 库

**问题描述**: 运行 CUDA 程序时提示找不到 libcuda.so 等库文件。

**可能原因**:
- CUDA 库路径未配置
- 库文件权限问题
- ldconfig 缓存未更新

**解决方案**:
1. 配置 LD_LIBRARY_PATH，在 `~/.bashrc` 中添加：
   ```bash
   export LD_LIBRARY_PATH=/usr/local/cuda/lib64:$LD_LIBRARY_PATH
   ```
2. 更新 ldconfig 缓存：
   ```bash
   echo "/usr/local/cuda/lib64" >> /etc/ld.so.conf.d/cuda.conf
   ldconfig
   ```
3. 确认库文件存在：`ls /usr/local/cuda/lib64/libcuda.so`
4. 验证配置：`ldconfig -p | grep cuda`

---

## Q3: conda 环境变量配置问题

**问题描述**: 激活 conda 环境后，原有 PATH 或库路径被覆盖。

**可能原因**:
- conda 环境激活脚本修改了 PATH
- 环境切换导致旧路径丢失
- conda 环境与系统环境冲突

**解决方案**:
1. 在 conda 环境配置文件中保留系统路径：
   ```bash
   conda config --add pkgs_dirs /opt/others
   ```
2. 修改环境变量配置，保留原有路径：
   ```bash
   export PATH=$PATH:/usr/local/cuda/bin
   export PATH=$PATH:~/anaconda3/bin
   ```
3. 使用 conda run 执行命令保持环境隔离
4. 考虑使用完整的绝对路径调用程序

---

## Q4: 环境变量生效后重启失效

**问题描述**: 在 shell 中配置的环境变量重启后丢失。

**可能原因**:
- 仅在当前 shell 会话中设置
- 未写入配置文件（如 .bashrc, /etc/profile）
- 系统更新覆盖了配置文件

**解决方案**:
1. 确认环境变量已写入配置文件：
   ```bash
   grep "VAR_NAME" ~/.bashrc
   ```
2. 系统级配置写入 `/etc/profile.d/`：
   ```bash
   echo "export VAR_NAME=value" >> /etc/profile.d/custom.sh
   chmod +x /etc/profile.d/custom.sh
   ```
3. 避免使用 `export VAR=value` 直接赋值
4. 验证配置文件加载顺序

---

## Q5: 容器内环境变量未传递

**问题描述**: 宿主机设置的环境变量在容器内不可见。

**可能原因**:
- 容器未使用 `--env` 或 `-e` 传递变量
- 容器基础镜像覆盖了环境变量
- Docker daemon 环境配置问题

**解决方案**:
1. 运行时传递环境变量：
   ```bash
   docker run -e VAR_NAME=value <image>
   ```
2. 或使用 env 文件：
   ```bash
   docker run --env-file=env.list <image>
   ```
3. 检查容器内环境变量：
   ```bash
   docker exec <container> env | grep VAR
   ```
4. 如需持久化变量，构建镜像时写入 Dockerfile 或 entrypoint 脚本
