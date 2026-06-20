# syntax=docker/dockerfile:1
FROM python:3.12-slim

# No .pyc files, unbuffered stdout/stderr for clean container logs.
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

# Install the package (plus uvicorn for serving) from the build context.
# Copy metadata + sources first so the layer caches on unchanged code.
COPY pyproject.toml README.md LICENSE ./
COPY src ./src
RUN pip install --no-cache-dir . "uvicorn>=0.29"

# Run as an unprivileged user.
RUN useradd --create-home --uid 10001 hodlbook
USER hodlbook

EXPOSE 8000

# create_app is a factory needing an injected client; hodlbook.cli:app_factory
# wraps it to resolve the boto3 client from the environment.
CMD ["uvicorn", "--factory", "hodlbook.cli:app_factory", \
     "--host", "0.0.0.0", "--port", "8000"]
