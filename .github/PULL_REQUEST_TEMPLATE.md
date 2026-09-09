## Summary

-

## Checklist

- [ ] No private photos, videos, local paths, credentials, or company information are included.
- [ ] File-moving behavior remains dry-run by default unless the user explicitly confirms execution.
- [ ] Core logic stays separate from CLI and GUI code.
- [ ] Tests or manual verification are included below.

## Verification

```bash
ruff check .
ruff format --check .
pytest tests/ -q
python -m py_compile phototidy/*.py phototidy/core/*.py
```

