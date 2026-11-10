FROM python:3.13-slim

WORKDIR /app

# ssh client: the fleet poller reads GPU temps from the rigs over a read-only forced command
RUN apt-get update && apt-get install -y --no-install-recommends openssh-client \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

RUN mkdir -p cache/whattomine cache/hashrateno cache/powerpool

EXPOSE 5000

CMD ["python", "app.py"]
