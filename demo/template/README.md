# errlens-demo

Demo repository for ErrLens: a tiny user/order service (Python) and a Node script.
Each bug was introduced by its own realistic commit, so blame always finds a meaningful commit.

| Command | Error | Actual cause |
|---|---|---|
| `make bug1` / `python -m shop profile 1` | `KeyError: 'email'` | A refactor renamed the API field to `email_address`; the profile code was not updated |
| `make bug2` / `node scripts/greet.js 1 2 3` | `TypeError: Cannot read properties of null` | `findUser` now returns `null` for inactive users |
| `make bug3` / `python -m shop report` | `ValueError: unconverted data remains: ...` | Date generation switched to ISO 8601; the report still parses the old format |

To apply a fix, save the diff from ErrLens as `fix.diff`:

```bash
python apply_fix.py bug3 fix.diff          # apply and re-run
python apply_fix.py bug3 fix.diff --revert # undo
```
