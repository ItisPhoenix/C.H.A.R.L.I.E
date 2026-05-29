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
