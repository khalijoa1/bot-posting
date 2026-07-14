FROM python:3.12-slim

WORKDIR /app

COPY pyproject.toml ./
COPY bot.py config.py db.py models.py middleware.py ./
COPY handlers ./handlers
COPY services ./services

RUN pip install --no-cache-dir .

CMD ["python", "bot.py"]
