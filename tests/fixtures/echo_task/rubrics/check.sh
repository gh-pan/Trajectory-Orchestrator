#!/usr/bin/env bash
test -f /workspace/done.txt && grep -q hello /workspace/done.txt
