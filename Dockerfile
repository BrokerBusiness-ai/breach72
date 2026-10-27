# Breach72: runs the agents offline (SYNTHETIC SQLite, replayed Nemotron) or against a live Zetaris (--env-file .env).
FROM python:3.11-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
ENV SAMPLE_MODE=true PYTHONUNBUFFERED=1
RUN python data/generator.py && python -m pytest -q
ENTRYPOINT ["python", "run.py"]
# Default: offline demo, all six cases on the local SYNTHETIC backend, Nemotron replayed from recordings.
CMD ["--all"]
