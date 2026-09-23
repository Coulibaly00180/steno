# PostgreSQL 17 (Alpine) + pgvector, for the local semantic search (phase 4).
#
# Same base image as before on purpose: the pgvector/pgvector images are
# Debian-based, and moving an existing data directory from musl to glibc
# changes text collation, which silently corrupts text indexes.
FROM postgres:17-alpine

ARG PGVECTOR_VERSION=0.8.6

# OPTFLAGS="": no -march=native, the image must run on any x86-64 CPU.
# with_llvm=no: no JIT bitcode, so no clang/LLVM matching the server's build.
RUN set -eux; \
    apk add --no-cache --virtual .build-deps build-base; \
    wget -qO /tmp/pgvector.tar.gz "https://github.com/pgvector/pgvector/archive/refs/tags/v${PGVECTOR_VERSION}.tar.gz"; \
    mkdir /tmp/pgvector; \
    tar -xzf /tmp/pgvector.tar.gz -C /tmp/pgvector --strip-components=1; \
    make -C /tmp/pgvector OPTFLAGS="" with_llvm=no; \
    make -C /tmp/pgvector OPTFLAGS="" with_llvm=no install; \
    rm -rf /tmp/pgvector /tmp/pgvector.tar.gz; \
    apk del .build-deps
