"""Entry point: ``python -m stubs._sdk_base`` starts the mock RunPod server on :8999.

Used by the runpod-sim Docker service in compose/sim.yml.
"""

from __future__ import annotations

import uvicorn

from stubs._sdk_base.mock_runpod import make_mock_runpod_app


def main() -> None:
    """Start the mock RunPod server, blocking until SIGTERM."""
    app = make_mock_runpod_app({"artifacts": [{"filename": "out.wav", "data": "AAEC"}]})
    uvicorn.run(app, host="0.0.0.0", port=8999)


if __name__ == "__main__":
    main()
