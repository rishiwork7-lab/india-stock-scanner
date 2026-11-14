FROM python:3.12-slim
ENV PYTHONUNBUFFERED=1 TZ=Asia/Kolkata
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
RUN mkdir -p data logs reports
EXPOSE 8790
# default: daily schedule at SCHEDULE_TIME (IST) with the web UI; override CMD for one-off runs
CMD ["python", "main.py", "--schedule", "--serve", "--host", "0.0.0.0", "--port", "8790"]
