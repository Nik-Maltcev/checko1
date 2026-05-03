FROM mcr.microsoft.com/playwright/python:v1.44.0-jammy

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt flask==3.0.3

RUN playwright install chromium --with-deps

COPY . .
RUN chmod +x start.sh

EXPOSE 5000

CMD ["bash", "start.sh"]
