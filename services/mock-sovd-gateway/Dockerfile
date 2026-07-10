FROM python:3.12-slim

WORKDIR /app

RUN pip install fastapi uvicorn --quiet

COPY main.py .

EXPOSE 7691

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "7691"]
