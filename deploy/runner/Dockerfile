ARG BASE_IMAGE=m.daocloud.io/docker.io/library/python:3.12-slim
FROM ${BASE_IMAGE}

ENV PLAYWRIGHT_BROWSERS_PATH=/ms-playwright

RUN pip install --no-cache-dir pytest==8.3.5 pytest-playwright==0.7.0 \
    && python -m playwright install --with-deps chromium \
    && chmod -R 755 /ms-playwright \
    && useradd --uid 10001 --create-home runner

USER 10001:10001
WORKDIR /workspace
