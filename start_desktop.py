"""Start the local native window without a browser or FastAPI server."""
import argparse
import os
from pathlib import Path

from bstom.desktop import main


if __name__ == "__main__":
    os.chdir(Path(__file__).resolve().parent)
    parser = argparse.ArgumentParser()
    parser.add_argument("--topic", default="")
    parser.add_argument("--auto-start", action="store_true")
    args = parser.parse_args()
    main(args.topic, args.auto_start)
