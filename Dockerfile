# 单镜像三种角色：harvester（gunicorn）/ mock-a / mock-b（标准库 http.server）
FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    HARVESTER_DB=/data/harvester.db

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY harvester ./harvester
COPY mock_oai ./mock_oai
COPY entrypoint.sh ./entrypoint.sh
RUN chmod +x ./entrypoint.sh && mkdir -p /data

EXPOSE 8000
ENTRYPOINT ["./entrypoint.sh"]
CMD ["harvester"]
