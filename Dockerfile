# la: lookup-only Linux command reference for Ubuntu 22.04 and 24.04.
#
# Stages 1 and 2 are full Ubuntu images used ONLY to collect man pages and
# command names. Nothing from them reaches the final image except plain-text
# files. The final image is Python on Alpine, with pip removed, flattened into
# one layer. The KB check runs during the build: one bad flag stops the build.
# After building: scripts/build.sh scans it with Trivy (no HIGH or CRITICAL).

# ===================== VERSIONS =====================
# Docker and kubectl are not stock Ubuntu packages. These are the versions
# whose man pages (docker) and --help text (docker compose, kubectl) la
# checks commands against. Match them to what the servers run.
ARG DOCKER_VERSION=28.5.1
ARG COMPOSE_VERSION=2.40.2
ARG KUBECTL_VERSION=1.34.13
# =================== END VERSIONS ===================

FROM ubuntu:22.04 AS man-2204
COPY build/packages.txt build/collect.sh /build/
RUN /build/collect.sh
ARG DOCKER_VERSION COMPOSE_VERSION KUBECTL_VERSION
COPY build/collect-extra.sh /build/
RUN /build/collect-extra.sh
COPY build/collect.py build/helpman.py /build/
RUN rel=$(. /etc/os-release; echo $VERSION_ID) \
 && python3 /build/collect.py "$rel" "/out/$rel" /tmp/skipped.txt \
 && python3 /build/helpman.py "/out/$rel" "$KUBECTL_VERSION" "$COMPOSE_VERSION"

FROM ubuntu:24.04 AS man-2404
COPY build/packages.txt build/collect.sh /build/
RUN /build/collect.sh
ARG DOCKER_VERSION COMPOSE_VERSION KUBECTL_VERSION
COPY build/collect-extra.sh /build/
RUN /build/collect-extra.sh
COPY build/collect.py build/helpman.py /build/
RUN rel=$(. /etc/os-release; echo $VERSION_ID) \
 && python3 /build/collect.py "$rel" "/out/$rel" /tmp/skipped.txt \
 && python3 /build/helpman.py "/out/$rel" "$KUBECTL_VERSION" "$COMPOSE_VERSION"

# Only the collected data, for working on the KB outside Docker:
#   docker build --target mandata --output type=local,dest=<dir> .
FROM scratch AS mandata
COPY --from=man-2204 /out/ /
COPY --from=man-2404 /out/ /

# The app: Python on Alpine. pip is removed (the base image's only HIGH
# findings are inside it), along with parts of Python the app never uses.
FROM python:3.13-alpine AS build
RUN apk upgrade --no-cache \
 && pip uninstall -y --root-user-action=ignore pip \
 && cd /usr/local/lib/python3.13 \
 && rm -rf ensurepip idlelib tkinter turtledemo turtle.py pydoc_data lib2to3 \
 && rm -rf /tmp/* /root/.cache
COPY --from=man-2204 /out/ /opt/la/data/
COPY --from=man-2404 /out/ /opt/la/data/
COPY src/la/ /opt/la/app/la/
COPY kb/ /tmp/kb/
# The KB check: every command installed, every flag in its man page.
# One problem stops the build here, with the problem named.
RUN PYTHONPATH=/opt/la/app python -m la.check --kb /tmp/kb --data /opt/la --out /opt/la/kb.json \
 && rm -rf /tmp/kb \
 && find / -xdev -name __pycache__ -type d -prune -exec rm -rf {} +

# One fresh layer, so nothing removed above is left in an older layer.
FROM scratch
COPY --from=build / /
ENV PATH=/usr/local/bin:/usr/bin:/bin LANG=C.UTF-8 \
    PYTHONPATH=/opt/la/app PYTHONDONTWRITEBYTECODE=1 LA_DATA=/opt/la
USER 65534
ENTRYPOINT ["python", "-m", "la.cli"]
