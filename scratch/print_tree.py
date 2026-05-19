import os
import sys

def print_tree(startpath, exclude_dirs):
    for root, dirs, files in os.walk(startpath):
        # Exclude specified directories
        dirs[:] = [d for d in dirs if d not in exclude_dirs]
        
        level = root.replace(startpath, '').count(os.sep)
        indent = ' ' * 4 * (level)
        print(f'{indent}{os.path.basename(root)}/')
        subindent = ' ' * 4 * (level + 1)
        for f in files:
            print(f'{subindent}{f}')

if __name__ == "__main__":
    exclude = {'.git', '.venv', '__pycache__', '.ruff_cache', '.uv-cache', '.pytest_cache', 'node_modules'}
    print_tree('.', exclude)
