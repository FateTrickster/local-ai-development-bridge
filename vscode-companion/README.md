# Local AI Development Bridge Companion

This VS Code extension is the local IDE companion for [Local AI Development Bridge](https://github.com/FateTrickster/local-ai-development-bridge).

It exposes a localhost-only HTTP companion API used by the Python bridge to read:

- VS Code diagnostics;
- current editor buffers, including unsaved changes;
- workspace/document symbols;
- definitions, references and implementations;
- hover information;
- provider readiness and workspace state.

The companion is not a standalone remote-control service. It listens only on `127.0.0.1` and requires the same local capability token used by the bridge.

## Development

```bash
npm ci
npm run compile
npm run package
```

The packaged `.vsix` can be installed with:

```bash
code --install-extension <file>.vsix --force
```

See the main repository README for bridge installation, one-click startup and security details.
