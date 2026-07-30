"""
PythonAnywhere WSGI entry point.

In PythonAnywhere Web tab → WSGI configuration file, point it to this file.
"""
import sys
import os
from pathlib import Path

PROJECT_DIR = Path('/home/tomdrayson/tomd_utilities')
sys.path.insert(0, str(PROJECT_DIR))
os.chdir(str(PROJECT_DIR))

from app import app as application  # noqa: F401  (PA expects 'application')
