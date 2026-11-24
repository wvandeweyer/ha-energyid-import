"""Constants shared by the tests."""

from typing import Final

from custom_components.energyid_import.const import API_BASE_URL

API_KEY: Final = "test-api-key-5f1c9e2a"
NEW_API_KEY: Final = "test-api-key-new-7b3d"
RECORD_ID: Final = "dummy-record-1"
RECORD_NAME: Final = "Dummy Home"
IMPORT_METER_ID: Final = "dummy-meter-1"
IMPORT_NIGHT_METER_ID: Final = "dummy-meter-2"
EXPORT_METER_ID: Final = "dummy-meter-3"
EXPORT_NIGHT_METER_ID: Final = "dummy-meter-4"

RECORDS_URL: Final = f"{API_BASE_URL}/members/me/records"
RECORD_URL: Final = f"{API_BASE_URL}/records/{RECORD_ID}"
METERS_URL: Final = f"{API_BASE_URL}/records/{RECORD_ID}/meters"
