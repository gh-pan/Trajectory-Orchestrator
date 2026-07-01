FROM alpine:3.20
RUN apk add --no-cache bash
WORKDIR /workspace
COPY hello.sh /workspace/hello.sh
RUN chmod +x /workspace/hello.sh
ENTRYPOINT ["tail", "-f", "/dev/null"]
