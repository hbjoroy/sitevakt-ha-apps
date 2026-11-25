FROM python:3.13-alpine
RUN pip install --no-cache-dir paho-mqtt==2.1.0
COPY watch.py dashboard.py /app/
CMD ["python", "-u", "/app/watch.py"]
