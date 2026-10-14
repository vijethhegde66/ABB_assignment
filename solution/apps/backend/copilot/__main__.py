"""Run the copilot backend: ``python -m copilot``."""

import uvicorn

from copilot.api import create_app
from copilot.config import CopilotSettings


def main() -> None:
    settings = CopilotSettings()
    uvicorn.run(create_app(settings), host=settings.host, port=settings.port, log_level="warning")


if __name__ == "__main__":
    main()
