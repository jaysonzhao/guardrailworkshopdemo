FROM registry.access.redhat.com/ubi9/python-312@sha256:e95978812895b9abb2bdc109b501078da2a47c8dbb9fa23758af40ed50ab6023
USER 0
COPY portal/ /opt/showroom/portal/
RUN python3 /opt/showroom/portal/install-ttyd.py && python3 -m pip install --no-cache-dir 'aiohttp==3.14.5' 'PyYAML==6.0.3' && chmod 0755 /opt/showroom/portal/start-shell
RUN command -v curl || (dnf -y install curl-minimal && dnf clean all)
COPY www/ /opt/showroom/www/
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 HOME=/tmp
USER 1001
CMD ["python3", "/opt/showroom/portal/server.py"]
