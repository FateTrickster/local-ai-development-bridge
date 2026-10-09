# Changelog

All notable changes to this project will be documented here.

## [Unreleased]

### Added

- Per-workspace permission policies (`inherit`, `readonly`, `write`, `command`, `full`) with global write/command permissions as a hard ceiling and Launcher `--workspace-policy ID=MODE`.
- Explicit multi-workspace registry with backward-compatible `default` workspace, workspace-scoped file/patch/PTY/VS Code operations, Dashboard aggregation, and Launcher `--extra-workspace ID=PATH`.
- Optional localhost-only human approval gate for file writes, patches and terminal commands, with one-time fingerprint-bound approvals, TTL and CSRF-resistant Dashboard decisions.
- Cross-platform persistent PTY backend for Linux/macOS using the Python standard library, with the same command lifecycle exposed on Windows.
- Bounded rotation for Activity, Progress and Audit JSONL logs with retained-history gap metadata.
- VS Code Companion semantic contract v2 for LSP queries, including explicit ready/empty/provider-unavailable/timeout/workspace states, document-open metadata and warmup retry visibility.
- Python-side normalization for legacy Companion LSP responses.

### Fixed

- pywinpty 2.0.x socket resources are explicitly closed after child exit, eliminating the repeated `ResourceWarning: unclosed socket` observed in terminal tests.

### Planned

- process-tree and CPU/memory observability
- richer Dashboard filtering and search

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
