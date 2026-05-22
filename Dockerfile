FROM python:3.11-slim-bookworm

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY megatool.py config.json proxy.json ./
COPY scripts/check_proxy_workability.py scripts/

RUN useradd --create-home --uid 1000 megatool \
    && mkdir -p /app/cache \
    && chown -R megatool:megatool /app

USER megatool

ENTRYPOINT ["python", "scripts/check_proxy_workability.py"]
CMD ["--smoke"]
