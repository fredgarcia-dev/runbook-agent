FROM python:3.12-slim

WORKDIR /app

# Install dependencies first (layer cached unless requirements.txt changes)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy source
COPY agents/       agents/
COPY observability/ observability/
COPY runbooks/     runbooks/
COPY scripts/      scripts/
COPY main.py       .

EXPOSE 8000

CMD ["python", "scripts/serve.py"]
