"""Shared pytest configuration.

Adds the functions/ directory to sys.path so tests can import project modules
directly (e.g. ``from text_extractor import ...``).
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "functions"))
