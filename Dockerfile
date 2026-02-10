FROM python:3.12-slim

RUN apt-get update && apt-get install -y git openssh-client && rm -rf /var/lib/apt/lists/*

# Create a non-root user (HF Spaces uses UID 1000)
RUN useradd -m -u 1000 user
USER user
ENV PATH="/home/user/.local/bin:${PATH}"

WORKDIR /app

COPY --chown=user:user requirements.txt .
RUN pip install --no-cache-dir --user -r requirements.txt

COPY --chown=user:user . .

# HF Spaces default port is 7860
EXPOSE 7860

# Start GAD using the config file
CMD ["python3", "-m", "gitautodeploy", "--config", "config.json"]
