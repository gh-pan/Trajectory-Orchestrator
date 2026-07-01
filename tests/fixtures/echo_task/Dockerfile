FROM node:22
RUN npm install -g @anthropic-ai/claude-code
RUN useradd -m -u 1001 agent
WORKDIR /workspace
COPY workspace /workspace
COPY rubrics /workspace/rubrics
RUN chmod +x /workspace/rubrics/check.sh \
    && chown -R agent:agent /workspace
USER agent
ENV HOME=/home/agent
ENTRYPOINT ["tail", "-f", "/dev/null"]
