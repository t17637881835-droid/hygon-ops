FROM harbor.sourcefind.cn:5443/dcu/admin/base/pytorch:2.7.1-kylin10sp3-dtk26.04-py3.10

WORKDIR /app

COPY docker/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 复制应用代码
COPY feishu_ops/ ./feishu_ops/
COPY skills/ ./skills/

# 创建日志和数据目录
RUN mkdir -p /app/logs /app/data

EXPOSE 8000

WORKDIR /app/feishu_ops
ENV PYTHONPATH=/app/feishu_ops

CMD ["python", "main.py"]
