"""Run the simulator: ``python -m alarm_simulator``."""

import uvicorn

from alarm_simulator.app import create_app
from alarm_simulator.config import SimulatorSettings


def main() -> None:
    settings = SimulatorSettings()
    uvicorn.run(create_app(settings), host=settings.host, port=settings.port, log_level="warning")


if __name__ == "__main__":
    main()
