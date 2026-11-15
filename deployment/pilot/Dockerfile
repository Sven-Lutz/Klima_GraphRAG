ARG PYTHON_IMAGE
FROM ${PYTHON_IMAGE}
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
COPY wheelhouse/ /wheels/
COPY requirements.lock /app/requirements.lock
# Owner-provided, target-platform wheelhouse; no network during image build.
RUN python -m pip install --no-index --find-links=/wheels --require-hashes -r requirements.lock
COPY source/ /app/
USER 10001:10001
