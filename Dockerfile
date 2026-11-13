FROM python:3.15-slim

ARG USER=kea

# git lets kea ingest repositories; nothing else is needed at run time
RUN apt-get update && apt-get install -y --no-install-recommends git && rm -rf /var/lib/apt/lists/* \
    && useradd -rms /bin/bash $USER

WORKDIR /app
COPY --chown=$USER:$USER src src
COPY --chown=$USER:$USER pyproject.toml README.md LICENSE ./
RUN --mount=source=.git,target=.git,type=bind \
    python3 -m pip install --no-cache-dir .

USER $USER
WORKDIR /brain
ENV KEA_BRAIN=/brain
# docker run --rm -it -v ~/brain:/brain kea status
ENTRYPOINT ["kea"]
CMD ["status"]
