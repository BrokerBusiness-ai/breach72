# Breach72: runs the agents in SAMPLE_MODE (replayed Zetaris responses) or against a live Zetaris.
FROM python:3.11-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
ENV SAMPLE_MODE=true PYTHONUNBUFFERED=1
RUN python data/generator.py && python -m pytest -q
ENTRYPOINT ["python", "run.py"]
CMD ["input_examples/case2_misdirected_email.json"]
