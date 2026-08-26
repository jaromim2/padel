FROM python:3.12-slim

ARG POSE_MODEL_URL=https://storage.googleapis.com/mediapipe-models/pose_landmarker/pose_landmarker_lite/float16/1/pose_landmarker_lite.task
ARG POSE_MODEL_SHA256=59929e1d1ee95287735ddd833b19cf4ac46d29bc7afddbbf6753c459690d574a
ARG POSE_MODEL_PATH=/opt/mediapipe-models/pose_landmarker_lite.task

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app \
    POSE_MODEL_PATH=${POSE_MODEL_PATH} \
    POSE_MODEL_URL=${POSE_MODEL_URL} \
    POSE_MODEL_SHA256=${POSE_MODEL_SHA256}

WORKDIR /app

RUN apt-get update && \
    apt-get install -y --no-install-recommends libxcb1 libgl1 libglib2.0-0 libgles2 libegl1 && \
    rm -rf /var/lib/apt/lists/*

COPY requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt

RUN mkdir -p "$(dirname "$POSE_MODEL_PATH")" && \
    python - <<'PY'
import hashlib
import os
import shutil
import tempfile
from pathlib import Path
from urllib.request import urlopen

url = os.environ["POSE_MODEL_URL"]
expected = os.environ["POSE_MODEL_SHA256"].lower()
target = Path(os.environ["POSE_MODEL_PATH"])
target.parent.mkdir(parents=True, exist_ok=True)

sha256 = hashlib.sha256()
with urlopen(url) as response, tempfile.NamedTemporaryFile(delete=False) as handle:
    temp_path = Path(handle.name)
    while True:
        chunk = response.read(1024 * 1024)
        if not chunk:
            break
        sha256.update(chunk)
        handle.write(chunk)

digest = sha256.hexdigest().lower()
if digest != expected:
    temp_path.unlink(missing_ok=True)
    raise SystemExit(f"POSE model checksum mismatch: {digest} != {expected}")

shutil.move(str(temp_path), target)
target.chmod(0o644)
PY

COPY app /app/app
COPY config /app/config
COPY tests /app/tests

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
