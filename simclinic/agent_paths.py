"""Constrain native model-proposed input paths before any directory is created."""
from pathlib import Path


def case_input_path(case, folder, filename):
    if folder not in {'0', 'constant', 'system'}:
        raise ValueError('Only initial fields, constants and system dictionaries are supported.')
    name = Path(filename)
    if not filename or name.name != filename or filename in {'.', '..'} or '\\' in filename:
        raise ValueError('Input file name must be a single relative leaf.')
    root = Path(case).resolve()
    path = root / folder / filename
    if path.is_symlink() or (root/folder).is_symlink() or not path.resolve().is_relative_to(root):
        raise ValueError('Input path must remain inside the case.')
    return path
