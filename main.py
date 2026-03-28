import logging
import sys

from app import VideoEditorApp

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")


def main():
    app = VideoEditorApp(sys.argv)
    sys.exit(app.run())


if __name__ == "__main__":
    main()
