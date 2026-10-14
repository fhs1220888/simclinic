FROM ghcr.io/astral-sh/uv:0.7.20@sha256:2fd1b38e3398a256d6af3f71f0e2ba6a517b249998726a64d8cfbe55ab34af5e AS uv
FROM openfoam/openfoam10-paraview510@sha256:d6ff1f9a2e7bc3c9177f373bebbdeb542fd8b49144afc24d5e3a3cd9bfae253d
USER root
COPY --from=uv /uv /usr/local/bin/uv
ENV UV_PYTHON_INSTALL_DIR=/opt/simclinic-python
RUN apt-get update && apt-get install -y --no-install-recommends git ca-certificates && rm -rf /var/lib/apt/lists/*
RUN uv python install 3.12.11 && /opt/simclinic-python/cpython-3.12.11-linux-x86_64-gnu/bin/python3.12 -m venv /opt/simclinic-venv
COPY simclinic-0.11.0+collect1-py3-none-any.whl /tmp/simclinic-0.11.0+collect1-py3-none-any.whl
RUN /opt/simclinic-venv/bin/python -m pip install --no-index --no-deps /tmp/simclinic-0.11.0+collect1-py3-none-any.whl && rm /tmp/simclinic-0.11.0+collect1-py3-none-any.whl
ENV PATH=/opt/simclinic-venv/bin:$PATH
WORKDIR /runs
ENTRYPOINT ["/bin/bash"]
