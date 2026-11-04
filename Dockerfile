FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    # Render terminates TLS at its proxy; trust X-Forwarded-For so the rate limit is per real client IP
    FORWARDED_ALLOW_IPS="*" \
    USE_LLM=0

RUN apt-get update \
    && apt-get install -y --no-install-recommends tesseract-ocr \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app
COPY frontend ./frontend
COPY samples ./samples
COPY README.md ./

RUN useradd --create-home --uid 10001 medlens && chown -R medlens:medlens /app
USER medlens

EXPOSE 8000
CMD python -m uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000} --proxy-headers
