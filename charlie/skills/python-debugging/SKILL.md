---
name: python-debugging
description: Python debugging methodology, common error patterns, traceback analysis, troubleshooting guide
metadata:
  version: "1.0.0"
  author: "system"
  icon: "🐛"
  inject_mode: "on_demand"
  tags: ["python", "debugging", "troubleshooting", "errors"]
---

# Python Debugging Methodology

## Purpose
Systematic approach to debugging Python code efficiently.

## Debug Process

### Step 1: Reproduce the Bug
- Identify the exact steps to reproduce
- Note the expected vs actual behavior
- Check if the bug is consistent or intermittent

### Step 2: Read the Traceback
- Start from the bottom (the actual error)
- Work up to find the root cause
- Note file paths and line numbers

### Step 3: Isolate the Problem
- Add print statements or use debugger
- Narrow down to the smallest failing case
- Check variable values at key points

### Step 4: Identify Root Cause
- Understand WHY the error occurs
- Check for common patterns (see common_errors.md)
- Look for recent changes that might have caused it

### Step 5: Fix and Verify
- Make the minimal fix that addresses root cause
- Test the fix in isolation
- Run full test suite to check for regressions
- Document the fix if it's a non-obvious pattern

## Quick Reference
- Always read the full traceback
- Start from the error, work backwards
- Isolate before fixing
- Test the fix, not just the symptom


# Common Python Errors

## TypeError
- **NoneType**: Variable is None, check initialization
- **Not callable**: Using () on non-function
- **Argument count**: Wrong number of args passed

## AttributeError
- **Missing method**: Typo or wrong class
- **None attribute**: Chained access on None object
- **Import issue**: Module not properly imported

## KeyError / IndexError
- **Key missing**: Dict key doesn't exist, use .get()
- **Out of range**: List index beyond length
- **Off-by-one**: Check loop bounds

## ImportError / ModuleNotFoundError
- **Typo**: Check module name spelling
- **Not installed**: pip install or uv add
- **Circular import**: Restructure imports

## IndentationError
- **Mixed tabs/spaces**: Use consistent indentation
- **Wrong level**: Check block structure

## FileNotFoundError
- **Wrong path**: Check relative vs absolute
- **Missing directory**: os.makedirs() first
- **Permissions**: Check file access rights


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
