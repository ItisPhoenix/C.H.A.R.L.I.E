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
