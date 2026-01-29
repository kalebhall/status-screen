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
        self.original_now_local = status_from_ics.now_local
        status_from_ics.TIMEZONE_NAME = "UTC"

    def tearDown(self):
        status_from_ics.TIMEZONE_NAME = self.original_timezone
        status_from_ics.now_local = self.original_now_local

    def set_now(self, when: datetime):
        status_from_ics.now_local = lambda tz: when.astimezone(tz)

    def build_work_hours(self, start="09:00", end="17:00", days="Mon-Fri"):
        return status_from_ics.build_work_hours_config(start, end, days)

    def test_all_day_non_ooo_is_ignored(self):
        self.set_now(datetime(2024, 1, 1, 12, tzinfo=timezone.utc))
        ics_text = build_all_day_ics("Company Holiday", "20240101", "20240102")
        self.assertIsNone(status_from_ics.current_calendar_event(ics_text))

    def test_all_day_ooo_is_included(self):
        self.set_now(datetime(2024, 1, 1, 12, tzinfo=timezone.utc))
        ics_text = build_all_day_ics("Out of Office", "20240101", "20240102")
        event = status_from_ics.current_calendar_event(ics_text)
        self.assertIsNotNone(event)
        self.assertEqual(event["name"], "Out of Office")

    def test_overlapping_events_pick_earliest_start(self):
        self.set_now(datetime(2024, 1, 1, 10, 30, tzinfo=timezone.utc))
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
        self.set_now(datetime(2024, 1, 1, 10, 0, tzinfo=timezone.utc))
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

    def test_working_hours_before_start_is_ooo(self):
        work_hours = self.build_work_hours()
        now = datetime(2024, 1, 1, 8, 30, tzinfo=timezone.utc)
        work_status = status_from_ics.working_hours_status(work_hours, now=now)
        self.assertIsNotNone(work_status)
        self.assertEqual(work_status["state"], "ooo")
        self.assertEqual(work_status["source"], "working_hours")
        self.assertEqual(work_status["until"], "2024-01-01T09:00:00+00:00")

    def test_working_hours_during_day_is_available(self):
        work_hours = self.build_work_hours()
        now = datetime(2024, 1, 1, 10, 0, tzinfo=timezone.utc)
        self.assertIsNone(status_from_ics.working_hours_status(work_hours, now=now))

    def test_working_hours_weekend_until_next_start(self):
        work_hours = self.build_work_hours()
        now = datetime(2024, 1, 6, 12, 0, tzinfo=timezone.utc)
        work_status = status_from_ics.working_hours_status(work_hours, now=now)
        self.assertIsNotNone(work_status)
        self.assertEqual(work_status["until"], "2024-01-08T09:00:00+00:00")

    def test_overnight_work_hours_span_midnight(self):
        work_hours = self.build_work_hours(start="22:00", end="06:00", days="Mon-Fri")
        now = datetime(2024, 1, 2, 1, 0, tzinfo=timezone.utc)
        self.assertIsNone(status_from_ics.working_hours_status(work_hours, now=now))
        now = datetime(2024, 1, 2, 12, 0, tzinfo=timezone.utc)
        self.assertIsNotNone(status_from_ics.working_hours_status(work_hours, now=now))

    def test_next_event_display_excludes_outside_work_hours(self):
        work_hours = self.build_work_hours()
        self.set_now(datetime(2024, 1, 1, 7, 30, tzinfo=timezone.utc))
        ics_text = "\n".join(
            [
                "BEGIN:VCALENDAR",
                "VERSION:2.0",
                "BEGIN:VEVENT",
                "UID:event-1",
                "DTSTAMP:20240101T083000Z",
                "DTSTART:20240101T083000Z",
                "DTEND:20240101T090000Z",
                "SUMMARY:Early Meeting",
                "END:VEVENT",
                "END:VCALENDAR",
            ]
        )
        self.assertIsNone(status_from_ics.next_event_for_display(ics_text, work_hours))


if __name__ == "__main__":
    unittest.main()
