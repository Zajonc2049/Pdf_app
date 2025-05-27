FROM python:3.11-slim

# Встановлюємо системні залежності для Tesseract та обробки зображень
RUN apt-get update && apt-get install -y \
    tesseract-ocr \
    tesseract-ocr-ukr \
    tesseract-ocr-eng \
    fonts-dejavu-core \
    && rm -rf /var/lib/apt/lists/*

# Створюємо директорію для шрифтів
RUN mkdir -p /app/fonts

# Копіюємо шрифт DejaVu (якщо у вас є локальна копія)
# COPY fonts/DejaVuSans.ttf /app/fonts/

# Альтернативно, створюємо симлінк на системний шрифт
RUN ln -sf /usr/share/fonts/truetype/dejavu/DejaVuSans.ttf /app/fonts/DejaVuSans.ttf

# Встановлюємо робочу директорію
WORKDIR /app

# Копіюємо файли залежностей
COPY requirements.txt .

# Встановлюємо Python залежності
RUN pip install --no-cache-dir -r requirements.txt

# Копіюємо код додатку
COPY . .

# Відкриваємо порт
EXPOSE 5000

# Команда запуску
CMD ["gunicorn", "--bind", "0.0.0.0:5000", "--workers", "2", "--timeout", "120", "app:app"]
