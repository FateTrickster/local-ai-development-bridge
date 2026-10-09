# Changelog

All notable changes to this project will be documented here.

## [Unreleased]

### Planned

- CI / Release automation
- LSP provider-state refinement
- PTY resource-warning cleanup
- broader cross-platform terminal support

## [0.1.0] - 2026-10-09

### Added

- safe workspace file browsing, reading, search and optimistic-version writes;
- transactional unified-diff patching;
- persistent Windows PTY commands with incremental output, stdin, wait and terminate;
- capability URL / Bearer authentication and audit logging;
- durable todo and progress journal;
- VS Code Companion for diagnostics, dirty buffers and language-provider queries;
- localhost-only observability dashboard with activity, progress and command timelines;
- one-click launcher with Cloudflare Quick Tunnel discovery, exact Host allowlist injection and local/public MCP smoke tests.

### Security

- runtime capability tokens and audit/runtime data are excluded from Git;
- public Tunnel state persisted to the dashboard does not include capability tokens;
- Activity and launcher state pass through redaction before persistence.
