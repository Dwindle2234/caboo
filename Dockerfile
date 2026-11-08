FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PORT=8000

WORKDIR /srv

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY tools/ tools/
COPY app/ app/

# (Re)generate the card art at build time so the SVGs always match the generator
RUN python tools/generate_cards.py app/static/cards

RUN useradd --create-home --uid 1000 cabo && chown -R cabo /srv
USER cabo

WORKDIR /srv/app
EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=3s --start-period=5s \
  CMD python -c "import urllib.request,os; urllib.request.urlopen('http://127.0.0.1:%s/healthz' % os.environ.get('PORT','8000'))" || exit 1

# ONE worker: game state lives in memory. Threads handle concurrent phones.
CMD ["sh", "-c", "gunicorn --workers 1 --threads 32 --bind 0.0.0.0:${PORT} --forwarded-allow-ips='*' --access-logfile - app:app"]
