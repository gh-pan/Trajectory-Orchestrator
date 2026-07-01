FROM node:22
RUN npm install -g @anthropic-ai/claude-code
WORKDIR /workspace
COPY workspace /workspace
COPY rubrics /workspace/rubrics
RUN chmod +x /workspace/rubrics/check.sh
ENTRYPOINT ["tail", "-f", "/dev/null"]
