ARG BASE_IMAGE=m.daocloud.io/docker.io/library/python:3.12-slim
FROM ${BASE_IMAGE}

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app/src

WORKDIR /app

COPY pyproject.toml ./
COPY src ./src
COPY postgres ./postgres
COPY config ./config

RUN pip install --no-cache-dir .

EXPOSE 8000

CMD ["uvicorn", "harness.api.app:create_app", "--factory", "--host", "0.0.0.0", "--port", "8000"]
