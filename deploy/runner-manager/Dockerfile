ARG BASE_IMAGE=m.daocloud.io/docker.io/library/python:3.12-slim
FROM ${BASE_IMAGE}

RUN apt-get update \
    && apt-get install -y --no-install-recommends docker-cli \
    && rm -rf /var/lib/apt/lists/* \
    && command -v docker

WORKDIR /app
COPY pyproject.toml ./
COPY src ./src
RUN pip install --no-cache-dir .

EXPOSE 8099
CMD ["python", "-m", "harness.execution.runner_manager_app"]
