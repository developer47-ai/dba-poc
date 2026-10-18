"""Start the Streamlit expert console: `python -m chat_ui`."""

import os
import sys
from pathlib import Path


def main() -> None:
    from streamlit.web import cli as streamlit_cli

    # Local-first defaults: no browser auto-open in containers and no usage telemetry.
    os.environ.setdefault("STREAMLIT_SERVER_HEADLESS", "true")
    os.environ.setdefault("STREAMLIT_BROWSER_GATHER_USAGE_STATS", "false")
    os.environ.setdefault("STREAMLIT_SERVER_PORT", os.getenv("CHAT_UI_PORT", "8501"))
    app = Path(__file__).with_name("app.py")
    sys.argv = ["streamlit", "run", str(app), *sys.argv[1:]]
    sys.exit(streamlit_cli.main())


if __name__ == "__main__":
    main()
