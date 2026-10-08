FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    KHDOOM_HOST=0.0.0.0 \
    KHDOOM_DB=/data/khdoom.db

WORKDIR /app
COPY requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt
COPY server.py /app/server.py
COPY owner_addons.js organization_addons.py owner_admin.py community_admin.py owner_dashboard.html owner_dashboard.js whatsapp_bridge.py twitter_integration.py /app/
COPY identity_directory.py vehicle_tracking.py /app/
COPY technical_support.py /app/technical_support.py
COPY ad_policy.py /app/ad_policy.py
COPY tenant_isolation.py /app/tenant_isolation.py
COPY branch_appointments.py /app/branch_appointments.py
COPY branch_sync.py /app/branch_sync.py
COPY appointment_context.py /app/appointment_context.py
COPY appointment_followups.py /app/appointment_followups.py
COPY customer_push.py /app/customer_push.py
COPY training_context.py /app/training_context.py
COPY reception_actions.py /app/reception_actions.py
COPY reception_conversations.py /app/reception_conversations.py
COPY ai_core.py /app/ai_core.py
COPY chat_store.py technical_agent.py /app/
COPY service_monitor.py owner_service_health.html /app/
COPY signup_offer.py owner_signup_offer.html /app/
COPY calls_trial.py owner_calls_trial.html /app/
COPY number_requests.py /app/number_requests.py
COPY call_gateway.py /app/call_gateway.py
COPY app_features.py /app/app_features.py
COPY admin_agent.py owner_agent.html /app/
COPY owner_ads.js /app/owner_ads.js
COPY package_limits.py owner_package_limits.html /app/
COPY service_quota.py /app/service_quota.py

RUN mkdir -p /data && useradd --create-home khdoom && chown -R khdoom:khdoom /app /data
USER khdoom

EXPOSE 8080
CMD ["python", "server.py"]


