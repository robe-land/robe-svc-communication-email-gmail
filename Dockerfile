FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

COPY pyproject.toml README.md ./
COPY src ./src

RUN python -m pip install --no-cache-dir .

RUN useradd --system --create-home app
USER app

CMD ["uvicorn", "robe_svc_communication_email_gmail.app:app", "--host", "0.0.0.0", "--port", "8000"]
