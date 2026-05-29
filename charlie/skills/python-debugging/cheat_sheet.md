# Python Debugging Cheat Sheet

## Quick Commands
- `print(type(var))` — Check variable type
- `print(repr(var))` — Show full representation
- `breakpoint()` — Drop into debugger
- `python -m pdb script.py` — Debug from start
- `python -m trace -t script.py` — Trace execution

## Common Patterns
- **None check**: `if var is None:` before using
- **Empty check**: `if not collection:` before iterating
- **Key check**: `if key in dict:` or `dict.get(key, default)`
- **Type check**: `isinstance(var, expected_type)`

## Debugging Tools
- `pdb` — Built-in debugger
- `ipdb` — Enhanced debugger with IPython
- `pytest -v` — Verbose test output
- `pytest -s` — Show print statements
- `pytest --pdb` — Drop to debugger on failure
