FROM python:3.12-slim

RUN apt-get update \
    && apt-get install -y --no-install-recommends g++ openjdk-21-jdk-headless \
    && rm -rf /var/lib/apt/lists/* \
    && useradd --create-home --shell /usr/sbin/nologin sandbox

WORKDIR /srv/sandbox
RUN pip install --no-cache-dir "fastapi>=0.119.0" "uvicorn[standard]>=0.38.0"
COPY app.py runner.py ./

USER sandbox
EXPOSE 8010
CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8010"]
