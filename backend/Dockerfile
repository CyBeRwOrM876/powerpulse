FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
RUN useradd -m -u 10001 pp
WORKDIR /srv
COPY backend/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY backend/ ./backend/
COPY frontend/ ./frontend/
ENV PP_DATA_DIR=/data PP_FRONTEND=/srv/frontend PP_SCHEDULE_CSV=/srv/backend/data/zesco_rationing_schedules_clean.csv
RUN mkdir /data && chown pp /data
USER pp
WORKDIR /srv/backend
EXPOSE 8000
HEALTHCHECK CMD python -c "import urllib.request;urllib.request.urlopen('http://127.0.0.1:8000/healthz')"
CMD ["uvicorn","app.main:app","--host","0.0.0.0","--port","8000","--proxy-headers"]
