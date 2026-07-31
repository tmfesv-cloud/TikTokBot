FROM python:3.12-slim

WORKDIR /app

# ffmpeg нужен для слияния аудио/видео в редких случаях (если TikTok отдаёт их раздельно)
RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .

RUN pip install --no-cache-dir -r requirements.txt

COPY . .

EXPOSE 8080

CMD ["python", "bot.py"]
