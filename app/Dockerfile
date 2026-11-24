FROM registry.access.redhat.com/ubi9/python-312@sha256:e95978812895b9abb2bdc109b501078da2a47c8dbb9fa23758af40ed50ab6023
WORKDIR /opt/app-root/src
COPY requirements.txt .
RUN python -m pip install --no-cache-dir -r requirements.txt
COPY . .
ENV HOME=/tmp PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
EXPOSE 8080
CMD ["python", "-m", "uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8080"]
