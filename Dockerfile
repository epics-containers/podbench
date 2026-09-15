# Developer tools and the build environment.
FROM ghcr.io/diamondlightsource/ubuntu-devcontainer:resolute AS developer

RUN apt-get update -y && apt-get install -y --no-install-recommends \
    graphviz \
    curl \
    ca-certificates \
    && apt-get dist-clean

# Match CI Helm and the pre-commit schema plugin; verify downloaded binaries.
ARG HELM_VERSION=v3.17.1
RUN arch="$(dpkg --print-architecture)" \
    && tarball="helm-${HELM_VERSION}-linux-${arch}.tar.gz" \
    && curl -fsSL "https://get.helm.sh/${tarball}" -o "/tmp/${tarball}" \
    && curl -fsSL "https://get.helm.sh/${tarball}.sha256sum" -o "/tmp/${tarball}.sha256sum" \
    && (cd /tmp && sha256sum -c "${tarball}.sha256sum") \
    && tar -xz -C /tmp -f "/tmp/${tarball}" \
    && mv /tmp/linux-*/helm /usr/local/bin/helm \
    && rm -rf /tmp/linux-* "/tmp/${tarball}" "/tmp/${tarball}.sha256sum" \
    && helm plugin install https://github.com/losisin/helm-values-schema-json \
    --version v2.5.0

# kubectl is the workstation CLI boundary to Kubernetes.
ARG KUBECTL_VERSION=v1.36.1
RUN arch="$(dpkg --print-architecture)" \
    && base="https://dl.k8s.io/release/${KUBECTL_VERSION}/bin/linux/${arch}" \
    && curl -fsSL "${base}/kubectl" -o /tmp/kubectl \
    && curl -fsSL "${base}/kubectl.sha256" -o /tmp/kubectl.sha256 \
    && echo "$(cat /tmp/kubectl.sha256)  /tmp/kubectl" | sha256sum -c - \
    && install -m 0755 /tmp/kubectl /usr/local/bin/kubectl \
    && rm -f /tmp/kubectl /tmp/kubectl.sha256 \
    && kubectl version --client

FROM developer AS build

WORKDIR /app
COPY . /app
RUN chmod o+wrX .

ENV UV_PYTHON_INSTALL_DIR=/python

RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --no-editable --no-dev --managed-python

# No libc dependency: hotfix targets supply syscall through their own loader.
RUN cc -shared -fPIC -nostdlib -fno-stack-protector \
    -o /libpodbench-ptrace.so src/podbench/ptrace_any.c

# The seat carries SSH, process tools, and the native debugger.
FROM debian:bookworm-slim AS runtime

COPY --from=build /libpodbench-ptrace.so /usr/local/lib/libpodbench-ptrace.so

RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates \
    openssh-server \
    openssh-sftp-server \
    openssh-client \
    libnss-extrausers \
    gdb \
    gdbserver \
    binutils \
    elfutils \
    debuginfod \
    procps \
    lsof \
    strace \
    less \
    git \
    curl \
    xz-utils \
    iproute2 \
    rsync \
    tini \
    && rm -rf /var/lib/apt/lists/*

# SSH host keys are generated in each seat by ssh_agent, never baked here.
RUN mkdir -p /run/sshd /etc/podbench

# Arbitrary non-root identities can register with NSS without capabilities.
# extrausers accepts UID >= 500 and GID >= 500 (or 100); GID 0 can use /etc/passwd.
# ssh_agent removes world write from the extrausers database in root seats.
RUN chmod g=u /etc/passwd /etc/group
RUN sed -i 's/^passwd:.*/passwd:         files extrausers/' /etc/nsswitch.conf \
    && mkdir -p /var/lib/extrausers \
    && touch /var/lib/extrausers/passwd \
    && chmod 0666 /var/lib/extrausers/passwd

# Preseed unused UIDs below the extrausers floor. Non-root SSH retains the
# process GID; 65534 is only a placeholder in the passwd record.
RUN set -eu; \
    for uid in $(seq 1 499); do \
        if ! getent passwd "$uid" >/dev/null; then \
            printf 'podbench-%s:x:%s:65534:podbench seat:/tmp/podbench-%s:/bin/bash\n' \
                "$uid" "$uid" "$uid"; \
        fi; \
    done >> /etc/passwd

# Hide generated hotfix state from Git and VS Code without editing the checkout.
RUN printf '[core]\n\texcludesFile = /etc/podbench/gitignore\n' > /etc/gitconfig \
    && printf '%s\n' '/.podbench-hotfix.json' '/.podbench-ptrace.so' \
        '/.podbench-tmp/' '/.python/' > /etc/podbench/gitignore

# Bound symbol downloads while GDB has the application stopped.
ENV DEBUGINFOD_URLS=https://debuginfod.debian.net

ENV DEBUGINFOD_TIMEOUT=2

# Use the standalone uv binary and reuse its managed Python installation.
COPY --from=ghcr.io/astral-sh/uv:0.12.15 /uv /usr/local/bin/uv

COPY --from=build /python /python
ENV UV_PYTHON_INSTALL_DIR=/python

COPY --from=build /app/.venv /app/.venv
ENV PATH=/app/.venv/bin:$PATH

# debugpy for `podbench ide vscode`: the seat runs debugpy's attach-to-pid with
# its own interpreter and copies this directory into the target's /tmp. It stays
# outside the venv so the podbench wheel keeps its two runtime dependencies.
RUN uv pip install --python /app/.venv/bin/python \
    --target /opt/podbench/debugpy debugpy==1.8.21

# SSH commands and login shells must both be able to find the CLI.
RUN ln -s /app/.venv/bin/podbench /usr/local/bin/podbench

RUN printf '%s\n' 'PATH="/app/.venv/bin:$PATH"' 'export PATH' \
    > /etc/profile.d/podbench.sh

# Check the managed interpreter works on the runtime base.
RUN podbench --version

ENTRYPOINT ["podbench"]
CMD ["--version"]
