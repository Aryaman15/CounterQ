FROM python:3.12-slim

RUN apt-get update \
    && apt-get install -y --no-install-recommends g++ \
    && rm -rf /var/lib/apt/lists/* \
    && useradd --create-home --shell /usr/sbin/nologin sandbox

WORKDIR /srv/sandbox
COPY app.py ./app.py
RUN pip install --no-cache-dir "fastapi>=0.119.0" "uvicorn[standard]>=0.38.0"

USER sandbox
EXPOSE 8010
CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8010"]
