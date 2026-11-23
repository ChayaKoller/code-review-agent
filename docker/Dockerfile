# Base image for running the tests of the reviewed project. Python + pytest only.
# The libraries of each reviewed repo are added on top of this image by
# core/docker_runner.py (one extra image per repo, built from its requirements files).
#
# Build:  python -m scripts.docker_check
# Behind NetFree you may need (in .env):
#   DOCKER_PIP_EXTRA_ARGS=--trusted-host pypi.org --trusted-host files.pythonhosted.org
ARG PYTHON_VERSION=3.12
FROM python:${PYTHON_VERSION}-slim

ARG PIP_EXTRA_ARGS=""
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

RUN pip install ${PIP_EXTRA_ARGS} pytest==8.3.4

# Run as a normal user, not root. The copy of the repo is mounted at /work.
RUN useradd --create-home --uid 1000 runner
USER runner
WORKDIR /work
