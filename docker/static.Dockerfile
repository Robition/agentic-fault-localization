# Static-arm image: bash, coreutils, grep, find, sed, and git. No Python.
# Alpine is used because Debian-based images ship Perl, which can run code.
# Build with: uv run execloc build-static
FROM alpine:3.20

RUN apk add --no-cache bash coreutils grep findutils sed git \
 && git config --system --add safe.directory '*'

# Fail the build if any general-purpose interpreter slipped in.
RUN for x in python python3 perl ruby node; do \
      if command -v "$x" >/dev/null 2>&1; then echo "unexpected interpreter: $x" >&2; exit 1; fi; \
    done

WORKDIR /testbed
