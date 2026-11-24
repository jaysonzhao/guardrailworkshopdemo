FROM registry.access.redhat.com/ubi9/python-312@sha256:e95978812895b9abb2bdc109b501078da2a47c8dbb9fa23758af40ed50ab6023 AS build
USER 0
RUN dnf -y install gcc-c++ python3.12-devel && dnf clean all
COPY requirements.txt /tmp/requirements.txt
RUN python -m venv /opt/rails-env && /opt/rails-env/bin/pip install --no-cache-dir -r /tmp/requirements.txt
FROM registry.access.redhat.com/ubi9/python-312@sha256:e95978812895b9abb2bdc109b501078da2a47c8dbb9fa23758af40ed50ab6023
COPY --from=build /opt/rails-env /opt/rails-env
ENV PATH=/opt/rails-env/bin:$PATH HOME=/tmp PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
EXPOSE 8000
ENTRYPOINT ["/opt/rails-env/bin/nemoguardrails"]
