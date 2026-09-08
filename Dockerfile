# Kullanılacak temel imaj (Python 3.10 ve Playwright bağımlılıkları içerir)
FROM mcr.microsoft.com/playwright/python:v1.42.0-jammy

# Çalışma dizinini ayarla
WORKDIR /app

# Gerekli dosyaları kopyala
COPY requirements.txt .

# Python kütüphanelerini kur
RUN pip install --no-cache-dir -r requirements.txt

# Kodları kopyala
COPY . .

# Botu çalıştır
CMD ["python", "main.py"]
