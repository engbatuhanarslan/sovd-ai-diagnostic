# services/diag-agent/Dockerfile
# SOVDpilot Diagnostic Agent container
# SPDX-License-Identifier: Apache-2.0

FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY diag_agent.py .

CMD ["python", "-u", "diag_agent.py"]
