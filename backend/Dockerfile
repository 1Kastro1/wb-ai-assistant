FROM python:3.12-slim
ENV PYTHONUNBUFFERED=1 WB_DATA_DIR=/data WB_CONTAINER=1 WB_SECRET_KEY_FILE=/run/secrets/wb_key
WORKDIR /app
COPY requirements.lock.txt .
RUN pip install --no-cache-dir -r requirements.lock.txt && useradd --uid 10001 --create-home assistant && mkdir /data && chown assistant /data
COPY . .
USER assistant
EXPOSE 8000
CMD ["python","-m","uvicorn","app.main:app","--host","0.0.0.0","--port","8000","--no-access-log"]
