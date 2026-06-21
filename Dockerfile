FROM python:3.12-slim
WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Cache-bust: bump APP_REV whenever the .py files change so the COPY layer below
# is never served from a stale Docker cache. (rev 3 — tab-aware tagger)
ARG APP_REV=3
RUN echo "Draftease build rev ${APP_REV}"
COPY redline_engine.py auth.py make_sample_lease.py tagger.py ai_extract.py app.py ./

EXPOSE 8000
# Hosts like Render/Railway inject $PORT; default to 8000 locally.
CMD ["sh", "-c", "uvicorn app:app --host 0.0.0.0 --port ${PORT:-8000}"]
