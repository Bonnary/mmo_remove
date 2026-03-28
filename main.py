import logging
import sys
from pathlib import Path

from app import VideoEditorApp

_log_file = Path(__file__).parent / "app.log"
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler(_log_file, encoding="utf-8"),
    ],
)


def main():
    app = VideoEditorApp(sys.argv)
    sys.exit(app.run())


if __name__ == "__main__":
    main()
