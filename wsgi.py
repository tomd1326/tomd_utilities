"""
PythonAnywhere WSGI entry point.

In PythonAnywhere Web tab → WSGI configuration file, point it to this file.
Replace '/home/tdrayson/expenditure' with your actual project path.
"""
import sys
import os
from pathlib import Path

PROJECT_DIR = Path('/home/tdrayson/expenditure')  # <-- change username if needed
sys.path.insert(0, str(PROJECT_DIR))
os.chdir(str(PROJECT_DIR))

from app import app as application  # noqa: F401  (PA expects 'application')
