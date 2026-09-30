"""Constants for the GeORG integration."""

from __future__ import annotations

from datetime import timedelta
import logging

DOMAIN = "georg"
LOGGER = logging.getLogger(__package__)

CONF_ROOMS = "rooms"
CONF_HEATERS = "heaters"
CONF_TEMPERATURE_SENSOR = "temperature_sensor"
CONF_HUMIDITY_SENSOR = "humidity_sensor"
CONF_SCAN_INTERVAL = "scan_interval"
CONF_FAILSAFE_GRACE = "failsafe_grace"

DEFAULT_SCAN_INTERVAL = 120  # seconds
MIN_SCAN_INTERVAL = 30
MAX_SCAN_INTERVAL = 900
DEFAULT_FAILSAFE_GRACE = 30  # minutes
MAX_FAILSAFE_GRACE = 720

# After this long without contact a repair issue is raised.
OUTAGE_ISSUE_AFTER = timedelta(hours=12)

STORAGE_VERSION = 1

ISSUE_CONNECTION_LOST = "connection_lost"
ISSUE_MODULE_MISSING = "module_missing"
ISSUE_UNKNOWN_ROOM = "unknown_room"
