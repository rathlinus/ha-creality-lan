DOMAIN = "creality_lan"

# HTTP & WS defaults
DEFAULT_PORT_HTTP = 80  # used by /info and snapshot
HEARTBEAT_SEC = 10
RECONNECT_BACKOFF = [1, 2, 5, 10, 20, 30]

# Printer state mapping (from your slicer snippet)
# 0=IDLE, 1=PRINTING, 2=COMPLETE, 3=FAILED, 4=ABORT, 5=PAUSED, 6=PAUSING, 7=STOPPING, 8=RESTORING
STATE_MAP = {
    None: "unknown",
    -1: "offline",
    0: "idle",
    1: "printing",
    2: "complete",
    3: "failed",
    4: "abort",
    5: "paused",
    6: "pausing",
    7: "stopping",
    8: "restoring",
}
