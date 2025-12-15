# استخدام نسخة بايثون خفيفة وحديثة
FROM python:3.10-slim

# منع بايثون من تخزين ملفات الكاش وتفعيل طباعة اللوج مباشرة
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

# إعداد مجلد العمل
WORKDIR /app

# تثبيت متطلبات النظام الضرورية (إن وجدت مستقبلاً)
# حالياً الكود يعمل بالمكتبات الأساسية، لكن قد نحتاج libjpeg للصور
RUN apt-get update && apt-get install -y \
    libjpeg-dev \
    zlib1g-dev \
    && rm -rf /var/lib/apt/lists/*

# نسخ ملف المتطلبات وتثبيتها
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# إنشاء مجلد البيانات للتخزين الدائم
RUN mkdir -p /app/data

# نسخ الكود المصدري
COPY main.py .

# أمر التشغيل
CMD ["python", "main.py"]
