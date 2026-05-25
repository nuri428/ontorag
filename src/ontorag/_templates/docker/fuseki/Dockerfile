# Multi-arch Fuseki image (linux/amd64 + linux/arm64)
# Uses eclipse-temurin:21-jre which has native ARM64 support —
# no Rosetta emulation needed on Apple Silicon.
FROM eclipse-temurin:21-jre-alpine

ARG FUSEKI_VERSION=5.0.0

RUN apk add --no-cache curl \
    && mkdir -p /opt/fuseki /fuseki/databases \
    && chmod -R 777 /fuseki/databases \
    && curl -fsSL \
       "https://archive.apache.org/dist/jena/binaries/apache-jena-fuseki-${FUSEKI_VERSION}.tar.gz" \
       | tar -xz -C /opt \
    && mv /opt/apache-jena-fuseki-${FUSEKI_VERSION}/* /opt/fuseki/ \
    && rm -rf /opt/apache-jena-fuseki-${FUSEKI_VERSION}

# Copy the jena-text assembler TEMPLATE. The entrypoint substitutes
# $FUSEKI_DATASET into it (dataset name + on-disk locations) so the dataset
# name stays configurable, then starts Fuseki with the generated config.
COPY docker/fuseki/config.ttl.template /fuseki/config.ttl.template

WORKDIR /opt/fuseki

ENV FUSEKI_DATASET=ontorag
ENV ADMIN_PASSWORD=admin

EXPOSE 3030

HEALTHCHECK --interval=10s --timeout=5s --retries=6 --start-period=40s \
    CMD curl -sf http://localhost:3030/$/ping || exit 1

# Use --config instead of --mem so jena-text (Lucene) is enabled. Substitute
# $FUSEKI_DATASET into the template (name + locations), create the per-dataset
# dirs, then start. urn:ontorag:* named graphs are NOT templated (fixed).
ENTRYPOINT ["sh", "-c", \
    "ds=\"${FUSEKI_DATASET:-ontorag}\"; \
     mkdir -p \"/fuseki/databases/${ds}\" \"/fuseki/databases/${ds}-lucene\"; \
     sed \"s|__FUSEKI_DATASET__|${ds}|g\" /fuseki/config.ttl.template > /fuseki/config.ttl; \
     chmod -R 777 /fuseki/databases 2>/dev/null; \
     java -Xmx512m -jar fuseki-server.jar \
          --port=3030 \
          --config=/fuseki/config.ttl"]
