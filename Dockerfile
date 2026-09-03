FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    KHDOOM_HOST=0.0.0.0 \
    KHDOOM_DB=/data/khdoom.db

WORKDIR /app
COPY requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt
COPY server.py /app/server.py
COPY ad_policy.py /app/ad_policy.py
COPY branch_appointments.py /app/branch_appointments.py
COPY appointment_context.py /app/appointment_context.py
COPY owner_ads.js /app/owner_ads.js
COPY package_limits.py owner_package_limits.html /app/

RUN mkdir -p /data && useradd --create-home khdoom && chown -R khdoom:khdoom /app /data
USER khdoom

EXPOSE 8080
CMD ["python", "server.py"]
