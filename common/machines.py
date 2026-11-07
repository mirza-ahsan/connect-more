"""The two machines of the experiments (ENVIRONMENT.md) and where their logs go."""

MACHINES = ("laptop1", "pc")  # the worker names
COORDINATOR, PEER = MACHINES  # Laptop 1 runs the coordinator; the PC is the other worker
PEER_DIR = PEER  # where the PC's logs are copied inside Laptop 1's logs folder (logs/pc/)
SENSORS_DIR = "sensors"  # each machine's sensor logs: logs/sensors/ (scripts/sensor_log.py)
