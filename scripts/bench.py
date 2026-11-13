"""
Measure how fast kea starts and answers, the way a person or an agent feels it.

    python scripts/bench.py

Runs each command 15 times against a fresh example brain and prints the best and the median wall time.
"""

import os
import statistics
import subprocess  # nosec B404: runs kea with fixed arguments only
import sys
import tempfile
import time
from pathlib import Path

RUNS = 15


def timed(args: list[str], env: dict[str, str]) -> tuple[float, float]:
    """
    Run a command several times.

    :param args: The command
    :param env: Its environment
    :returns: The best and the median time, in milliseconds
    """
    times = []
    for _ in range(RUNS):
        start = time.perf_counter()
        subprocess.run(args, env=env, check=True, capture_output=True)  # nosec B603: fixed arguments
        times.append((time.perf_counter() - start) * 1000)
    return min(times), statistics.median(times)


def main() -> int:
    """
    Make an example brain and time the common commands.

    :returns: Exit code
    """
    kea = [sys.executable, "-m", "kea"]
    with tempfile.TemporaryDirectory() as tmp:
        brain = Path(tmp) / "brain"
        env = {**os.environ, "XDG_CONFIG_HOME": str(Path(tmp) / "config"), "NO_COLOR": "1"}
        subprocess.run(
            [*kea, "init", str(brain), "--example", "--no-git"], env=env, check=True, capture_output=True
        )  # nosec B603
        env["KEA_BRAIN"] = str(brain)
        cases = {
            "python -c pass": [sys.executable, "-c", "pass"],
            "kea --version": [*kea, "--version"],
            "kea status": [*kea, "status"],
            "kea search": [*kea, "search", "wholesale", "--no-refresh"],
            "kea predictions --json": [*kea, "predictions", "--json"],
        }
        subprocess.run(
            [*kea, "search", "warm"], env=env, check=True, capture_output=True
        )  # nosec B603: builds the index
        for name, args in cases.items():
            best, median = timed(args, env)
            print(f"{name:<26} best {best:6.1f} ms   median {median:6.1f} ms")
    return 0


if __name__ == "__main__":
    sys.exit(main())
