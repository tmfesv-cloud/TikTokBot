FROM python:3.12-slim

WORKDIR /app

# ffmpeg нужен для слияния аудио/видео и водяного знака (drawtext)
# fonts-dejavu-core — шрифт для текстового знака на видео
RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg fonts-dejavu-core \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .

RUN pip install --no-cache-dir -r requirements.txt

COPY . .

EXPOSE 8080

CMD ["python", "bot.py"]
