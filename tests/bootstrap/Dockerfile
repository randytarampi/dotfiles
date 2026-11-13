# Optional unprivileged Ubuntu reproduction; build using git archive HEAD.
ARG UBUNTU_VERSION=24.04
FROM ubuntu:${UBUNTU_VERSION}
ENV DEBIAN_FRONTEND=noninteractive
RUN apt-get update && apt-get install -y --no-install-recommends \
    bash ca-certificates curl git make python3 passwd \
    && rm -rf /var/lib/apt/lists/*
SHELL ["/bin/bash", "-o", "pipefail", "-c"]
RUN groupadd --gid 10001 dotfiles && \
    useradd --uid 10001 --gid 10001 --create-home --shell /bin/bash dotfiles
COPY --chown=10001:10001 . /workspace/dotfiles
USER 10001:10001
WORKDIR /workspace/dotfiles
ENV HOME=/home/dotfiles
ENV PATH=/home/dotfiles/.local/bin:$PATH
RUN mkdir -p "$HOME/.local/bin" && \
    curl -fsSL https://get.chezmoi.io | sh -s -- -b "$HOME/.local/bin" -t v2.72.2
CMD ["bash", "tests/bootstrap/smoke.sh"]
