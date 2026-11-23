# numtrail web — one container that serves the UI *and* the API on one origin,
# so there is no CORS to configure and no separate frontend to point anywhere.
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PORT=8000

WORKDIR /app

COPY pyproject.toml README.md ./
COPY numtrail ./numtrail
RUN pip install --no-cache-dir .

# Keys are optional; pass any of these with -e / the host's env settings:
#   TWILIO_ACCOUNT_SID TWILIO_AUTH_TOKEN NUMVERIFY_KEY ABSTRACTAPI_PHONE_KEY
#   VERIPHONE_KEY IPQS_KEY VONAGE_API_KEY VONAGE_API_SECRET TELESIGN_* PLIVO_*
#   BIRD_ACCESS_KEY NUMLOOKUPAPI_KEY TRESTLE_API_KEY MOCEAN_*

RUN useradd --create-home --uid 10001 numtrail
USER numtrail

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=5s \
  CMD python -c "import urllib.request,os; urllib.request.urlopen(f'http://127.0.0.1:{os.environ.get(\"PORT\",\"8000\")}/healthz', timeout=4)"

CMD ["sh", "-c", "numtrail-web --host 0.0.0.0 --port ${PORT:-8000}"]
