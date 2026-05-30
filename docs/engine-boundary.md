# Public engine boundary

This repository publishes the **BaseTool core engine** only:

- `basetool.py`
- `config.json`
- `proxy.json`
- `requirements.txt`

## What belongs here

Portable attack/runtime logic, public configuration defaults, and dependency lists that are safe to open-source.

## What does not belong here

Application shells, encrypted runtime packaging, release automation, remote update feeds, deployment scripts, credentials, or any environment-specific infrastructure.

Downstream products may vendor these files and apply their own integration patches locally. Those patches should stay outside this repository unless they are intentionally contributed back as portable engine improvements.

## Contributing engine changes

1. Keep changes limited to the files listed above.
2. Avoid embedding product-specific integration, secrets, or deployment assumptions.
3. Open a pull request with a clear description of the engine behavior change.

Maintainers of downstream integrations are responsible for reconciling their local patches after engine updates land here.
