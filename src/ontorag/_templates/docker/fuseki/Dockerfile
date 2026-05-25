# Multi-arch Fuseki image (linux/amd64 + linux/arm64)
# Uses eclipse-temurin:21-jre which has native ARM64 support —
# no Rosetta emulation needed on Apple Silicon.
FROM eclipse-temurin:21-jre-alpine

ARG FUSEKI_VERSION=5.0.0

RUN apk add --no-cache curl \
    && mkdir -p /opt/fuseki /fuseki/databases/ontorag /fuseki/databases/ontorag-lucene \
    && chmod -R 777 /fuseki/databases \
    && curl -fsSL \
       "https://archive.apache.org/dist/jena/binaries/apache-jena-fuseki-${FUSEKI_VERSION}.tar.gz" \
       | tar -xz -C /opt \
    && mv /opt/apache-jena-fuseki-${FUSEKI_VERSION}/* /opt/fuseki/ \
    && rm -rf /opt/apache-jena-fuseki-${FUSEKI_VERSION}

# Copy the jena-text assembler config into the image.
# The config defines a TDB2 + Lucene TextDataset at /fuseki/databases/.
COPY docker/fuseki/config.ttl /fuseki/config.ttl

WORKDIR /opt/fuseki

ENV FUSEKI_DATASET=ontorag
ENV ADMIN_PASSWORD=admin

EXPOSE 3030

HEALTHCHECK --interval=10s --timeout=5s --retries=6 --start-period=40s \
    CMD curl -sf http://localhost:3030/$/ping || exit 1

# Use --config instead of --mem so jena-text (Lucene) is enabled.
# /fuseki/databases/ is writable at build time; chmod again at runtime to
# handle volume mounts that may arrive without write permission.
ENTRYPOINT ["sh", "-c", \
    "chmod -R 777 /fuseki/databases 2>/dev/null; \
     java -Xmx512m -jar fuseki-server.jar \
          --port=3030 \
          --config=/fuseki/config.ttl"]
