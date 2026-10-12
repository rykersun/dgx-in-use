FROM python:3-alpine
RUN apk add --no-cache tzdata
ENV TZ=Asia/Taipei
COPY app.py /app.py
EXPOSE 8901
CMD ["python3", "app.py"]
