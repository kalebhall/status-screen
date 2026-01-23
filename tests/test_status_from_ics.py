import sys
import unittest
import importlib.util
from datetime import datetime, timezone
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parents[1]))

HAS_DEPS = bool(importlib.util.find_spec("dateutil")) and bool(
    importlib.util.find_spec("ics")
)
if HAS_DEPS:
    from pi import status_from_ics
else:
    status_from_ics = None


def build_all_day_ics(summary: str, start_date: str, end_date: str) -> str:
    return "\n".join(
        [
            "BEGIN:VCALENDAR",
            "VERSION:2.0",
            "BEGIN:VEVENT",
            "UID:1",
            f"DTSTART;VALUE=DATE:{start_date}",
            f"DTEND;VALUE=DATE:{end_date}",
            f"SUMMARY:{summary}",
            "END:VEVENT",
            "END:VCALENDAR",
        ]
    )


@unittest.skipUnless(HAS_DEPS, "requires dateutil and ics")
class StatusFromIcsTests(unittest.TestCase):
    def setUp(self):
        self.original_timezone = status_from_ics.TIMEZONE_NAME
        self.original_now = status_from_ics.now_utc
        status_from_ics.TIMEZONE_NAME = "UTC"

    def tearDown(self):
        status_from_ics.TIMEZONE_NAME = self.original_timezone
        status_from_ics.now_utc = self.original_now

    def test_all_day_non_ooo_is_ignored(self):
        status_from_ics.now_utc = lambda: datetime(2024, 1, 1, 12, tzinfo=timezone.utc)
        ics_text = build_all_day_ics("Company Holiday", "20240101", "20240102")
        self.assertIsNone(status_from_ics.current_calendar_event(ics_text))

    def test_all_day_ooo_is_included(self):
        status_from_ics.now_utc = lambda: datetime(2024, 1, 1, 12, tzinfo=timezone.utc)
        ics_text = build_all_day_ics("Out of Office", "20240101", "20240102")
        event = status_from_ics.current_calendar_event(ics_text)
        self.assertIsNotNone(event)
        self.assertEqual(event["name"], "Out of Office")

    def test_overlapping_events_pick_earliest_start(self):
        status_from_ics.now_utc = lambda: datetime(2024, 1, 1, 10, 30, tzinfo=timezone.utc)
        ics_text = "\n".join(
            [
                "BEGIN:VCALENDAR",
                "VERSION:2.0",
                "BEGIN:VEVENT",
                "UID:event-1",
                "DTSTAMP:20240101T090000Z",
                "DTSTART:20240101T090000Z",
                "DTEND:20240101T110000Z",
                "SUMMARY:Standup",
                "END:VEVENT",
                "BEGIN:VEVENT",
                "UID:event-2",
                "DTSTAMP:20240101T100000Z",
                "DTSTART:20240101T100000Z",
                "DTEND:20240101T120000Z",
                "SUMMARY:Planning",
                "END:VEVENT",
                "END:VCALENDAR",
            ]
        )
        event = status_from_ics.current_calendar_event(ics_text)
        self.assertIsNotNone(event)
        self.assertEqual(event["name"], "Standup")

    def test_event_end_is_exclusive(self):
        status_from_ics.now_utc = lambda: datetime(2024, 1, 1, 10, 0, tzinfo=timezone.utc)
        ics_text = "\n".join(
            [
                "BEGIN:VCALENDAR",
                "VERSION:2.0",
                "BEGIN:VEVENT",
                "UID:event-1",
                "DTSTAMP:20240101T090000Z",
                "DTSTART:20240101T090000Z",
                "DTEND:20240101T100000Z",
                "SUMMARY:Wrap-up",
                "END:VEVENT",
                "END:VCALENDAR",
            ]
        )
        self.assertIsNone(status_from_ics.current_calendar_event(ics_text))


if __name__ == "__main__":
    unittest.main()
