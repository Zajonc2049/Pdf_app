FROM python:3.11-slim

# Встановлення залежностей
RUN apt-get update && apt-get install -y \
    tesseract-ocr \
    tesseract-ocr-ukr \
    libjpeg-dev \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/
    fonts/DejaVuSans.ttf*

WORKDIR /app

COPY . /app

RUN pip install --no-cache-dir -r requirements.txt

CMD ["python", "bot.py"]
