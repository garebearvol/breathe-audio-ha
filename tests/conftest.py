"""Configure pytest to find modules correctly."""
import sys
import os

# Add the project root to sys.path so tests can import serial_manager, const, etc.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
