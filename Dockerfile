FROM python:3.9-alpine

WORKDIR /app

COPY requirements.txt .  # <-- КОПИРОВАНИЕ
RUN pip install --no-cache-dir -r requirements.txt  # <-- УСТАНОВКА

COPY bot.py .

CMD ["python", "bot.py"]
