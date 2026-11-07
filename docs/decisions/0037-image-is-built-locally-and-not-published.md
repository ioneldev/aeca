# 0037: The image is built locally and not published

**Who decided:** Claude. 2026-11-05.

**Decision.**
- `make image` builds the image locally from a multi-arch base image, so it runs on arm64 and amd64. No image is published.
- `HTTPS_PROXY` is passed through when it is set. The README says Windows needs WSL for `make`.

**Why.** The spec gives no reason for building locally. §10 says only that the image builds locally for arm64 and amd64.

**Rejected.**
- Publishing an image (inferred).

**Cost.** The spec states none.

**Source:** [the spec](../superpowers/specs/2026-11-05-aeca-design.md), §4.11 and §10.
