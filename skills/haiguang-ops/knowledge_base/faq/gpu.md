# GPU/驱动问题 FAQ

## Q1: nvidia-smi 显示 "No devices were found"

**问题描述**: 执行 nvidia-smi 命令显示找不到 GPU 设备。

**可能原因**:
- NVIDIA 驱动未正确安装
- GPU 硬件未识别
- 驱动版本与 CUDA 版本不匹配
- 容器未正确配置 GPU 访问

**解决方案**:
1. 检查驱动安装状态：
   ```bash
   lsmod | grep nvidia
   ```
2. 重新安装 NVIDIA 驱动
3. 检查 GPU 硬件识别：
   ```bash
   lspci | grep -i nvidia
   ```
4. 如使用容器，确认使用了 nvidia 运行时：
   ```bash
   docker run --runtime=nvidia <image>
   ```

---

## Q2: GPU 频率过低或自动降频

**问题描述**: nvidia-smi 显示 GPU 频率远低于标称频率，性能下降。

**可能原因**:
- GPU 温度过高触发了保护机制
- 电源功率不足
- 驱动或 CUDA 版本的功耗管理配置

**解决方案**:
1. 检查 GPU 温度：`nvidia-smi -q -g Temperature`
2. 清理 GPU 散热器灰尘，加强散热
3. 检查电源供应是否满足 GPU 需求
4. 禁用驱动中的降频选项：
   ```bash
   nvidia-smi -pl 350  # 设置最大功耗
   ```
5. 检查是否有进程抢占了 GPU 资源

---

## Q3: CUDA 版本不兼容

**问题描述**: 运行 CUDA 程序时报错，提示版本不匹配。

**可能原因**:
- 驱动版本不支持 CUDA 版本
- CUDA 库路径配置错误
- 多个 CUDA 版本共存导致冲突

**解决方案**:
1. 检查驱动支持的 CUDA 版本：`nvidia-smi`
2. 确认系统 CUDA 版本：`nvcc --version`
3. 设置正确的 CUDA 库路径：
   ```bash
   export LD_LIBRARY_PATH=/usr/local/cuda/lib64:$LD_LIBRARY_PATH
   export PATH=/usr/local/cuda/bin:$PATH
   ```
4. 如有多个 CUDA 版本，使用 update-alternatives 选择
5. 必要时升级驱动或降级 CUDA 版本

---

## Q4: 驱动安装失败

**问题描述**: 安装 NVIDIA 驱动时失败，提示编译错误或签名问题。

**可能原因**:
- 内核版本不匹配
- 缺少内核开发头文件
- Secure Boot 签名阻止驱动加载
- 禁用 Nouveau 驱动失败

**解决方案**:
1. 确认内核版本：`uname -r`
2. 安装内核开发包：
   ```bash
   yum install -y kernel-devel kernel-headers
   ```
3. 禁用 Nouveau 驱动：
   ```bash
   echo "blacklist nouveau" >> /etc/modprobe.d/blacklist.conf
   grub2-mkconfig -o /boot/grub2/grub.cfg
   reboot
   ```
4. 如有 Secure Boot，需关闭或对驱动签名
5. 查看安装日志获取具体错误信息

---

## Q5: 多块 GPU 但只识别部分

**问题描述**: 服务器有多块 GPU，但 nvidia-smi 只显示部分 GPU。

**可能原因**:
- GPU 物理位置或拓扑问题
- PCIe 插槽接触不良
- 驱动或硬件问题

**解决方案**:
1. 检查所有 GPU 状态：`lspci | grep -i nvidia`
2. 重新安装驱动
3. 检查 PCIe 插槽是否牢固
4. 查看 dmesg 日志是否有 GPU 错误信息
5. 如持续问题，联系硬件供应商支持
