# Appended to the canonical Dockerfile: retain its locked PM dependencies,
# SQLite fix and compiled frontends, without inheriting its /opt/data volume.
FROM runtime_base AS agentdesk_image
COPY --from=runtime /opt/hermes /opt/hermes
ARG SOURCE_REVISION
RUN cd /opt/hermes && python3 scripts/write_install_stamp.py --output install-stamp.json \
    --commit "$SOURCE_REVISION" --source ci --distribution docker --update-mechanism external \
 && mkdir -p /var/lib/hermes/.hermes/installs/bcb380a4495ec6cc/environments/f878407c2d4f434794c12650f8fc54c4/venv/bin \
 && ln -s /opt/hermes/.venv/bin/python /var/lib/hermes/.hermes/installs/bcb380a4495ec6cc/environments/f878407c2d4f434794c12650f8fc54c4/venv/bin/python \
 && printf '#!/bin/sh\ncd /opt/hermes\nexec /opt/hermes/.venv/bin/python -m hermes_cli.main "$@"\n' > /usr/local/bin/hermes \
 && chmod 0755 /usr/local/bin/hermes
ENV HERMES_PYTHON=/opt/hermes/.venv/bin/python HERMES_WEB_DIST=/opt/hermes/hermes_cli/web_dist
ENV HERMES_HOME=/state HERMES_WRITE_SAFE_ROOT=/state
ENV PATH=/opt/hermes/.venv/bin:/usr/local/bin:/usr/bin:/bin
WORKDIR /opt/hermes
LABEL org.opencontainers.image.revision=$SOURCE_REVISION
USER 10000:10000
ENTRYPOINT []
CMD ["/usr/local/bin/hermes", "--help"]
