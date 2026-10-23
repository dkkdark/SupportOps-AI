FROM python:3.13-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1
WORKDIR /app
COPY requirements.lock pyproject.toml ./
RUN pip install --no-cache-dir -r requirements.lock
COPY requirements-build.lock ./
# Pin and hash the build backend; packaging the copied sources then needs no index.
RUN pip install --no-cache-dir --no-index --no-deps -r requirements-build.lock \
    "https://files.pythonhosted.org/packages/ef/55/6dc6d6bb953bdfc52519a0059592c52b6e3b8ad51cf8256dffceb55b0610/setuptools-84.2.1-py3-none-any.whl#sha256=ca140b45d5325a9478f880e351484f62fef15f336462e449ca0c03c873d589c2"
COPY supportops ./supportops
COPY data ./data
COPY eval ./eval
COPY scripts ./scripts
COPY migrations ./migrations
COPY alembic.ini ./
RUN pip install --no-deps --no-build-isolation . && useradd --uid 10001 --create-home app && mkdir -p /app/var /app/reports && chown -R app:app /app
USER 10001
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=4s --start-period=20s --retries=3 CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health/live', timeout=2)"
CMD ["uvicorn", "supportops.main:app", "--host", "0.0.0.0", "--port", "8000"]
