FROM python:3.13-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

RUN mkdir -p cache/whattomine cache/hashrateno cache/miningnow cache/powerpool

EXPOSE 5000

CMD ["python", "app.py"]
