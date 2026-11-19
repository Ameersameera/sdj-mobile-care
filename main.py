"""SDJ MOBILE CARE – SMART SHOP MANAGEMENT SYSTEM

Run:  python main.py            (your real shop data)
      python main.py --demo     (sample data, kept separate; login admin / admin123)
"""
import sys

from sdj.ui.app import run

if __name__ == "__main__":
    sys.exit(run())
