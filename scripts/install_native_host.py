"""Optional manual repair; normal CLI use registers automatically."""
import argparse
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from video_notes.native_setup import ensure_registered, EXTENSION_ID

if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('extension_id', nargs='?', default=EXTENSION_ID)
    args = parser.parse_args()
    ensure_registered(args.extension_id)
    print('本机通信注册完成。')
