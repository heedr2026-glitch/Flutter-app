FROM python:3.13-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 KHDOOM_HOST=0.0.0.0 KHDOOM_DB=/data/khdoom.db
WORKDIR /app
COPY backend/server.py /app/server.py
RUN mkdir -p /data && useradd --create-home khdoom && chown -R khdoom:khdoom /app /data
USER khdoom
EXPOSE 8080
CMD ["python", "server.py"]
