#!/bin/sh
# Forces the already-running dashboard (main.py) to immediately re-fetch
# every enabled data source, ignoring each one's normal polling interval —
# useful when you want a fresh read right now (e.g. she just logged water,
# or you just fixed a config file) rather than waiting for the next
# scheduled poll. Safe to run any time; it's a no-op if main.py isn't
# running. See force_refresh_all() in main.py for what this actually does.
pkill -USR1 -f "python3? .*main\.py" && echo "Force-refresh requested." || echo "main.py doesn't appear to be running."
