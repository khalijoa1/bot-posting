FROM python:3.12-slim

WORKDIR /app

COPY pyproject.toml ./
COPY bot.py config.py db.py models.py middleware.py ./
COPY handlers ./handlers
COPY services ./services
COPY migrations ./migrations
COPY scripts ./scripts

RUN pip install --no-cache-dir .
RUN chmod +x scripts/run.sh

CMD ["./scripts/run.sh"]

