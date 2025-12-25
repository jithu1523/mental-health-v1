# Contributing

Thanks for considering contributing to MindTriage.

## Setup

```bash
cp .env.example .env
```

Run:
```bash
./scripts/run_dev.sh
```

Windows:
```powershell
.\scripts\run_dev.ps1
```

## Tests

```bash
python -m unittest discover -s mindtriage/backend/tests
```

## Coding style

- Keep changes minimal and readable.
- Avoid new dependencies unless needed.
- Keep local-first behavior intact.
- Use ASCII unless a file already uses Unicode.
