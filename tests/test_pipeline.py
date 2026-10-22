"""Regressies: dezelfde fouten mogen bij een nieuwe week niet terugkomen."""
from __future__ import annotations

import unittest
from datetime import date
from io import BytesIO

from pypdf import PdfReader

from kpt.storage import merge_file_catalog
from kpt.core import (
    _allocate_plan_km_by_country,
    _correct_french_km,
    _fill_incomplete_french_km,
    _assign_plan,
    _plan_index,
    _resolve_plan,
    collapse_detail_summaries,
    dedupe_report_rows,
    dmy_iso,
    parse_all_reports,
)
from maak_pdf_per_klant import make_pdf_bytes


def trx(**kwargs):
    row = {
        "id": "",
        "date": "21/09/2026",
        "time": "10:00:00",
        "plate": "95BPS8",
        "plateRaw": "95-BPS-8",
        "km": 1.0,
        "eur": 1.0,
        "road": "",
        "entry": "",
        "exit": "",
        "tsp": "",
        "country": "DE",
        "billing": "Billed",
    }
    row.update(kwargs)
    return row


class PipelineTests(unittest.TestCase):
    def test_session_keeps_other_week_files(self):
        previous = {
            "Week 39-2026.xls": {"mtime": 1, "kind": "planning", "size": 10},
            "rapport.csv": {"mtime": 2, "kind": "report", "size": 20},
        }
        incoming = {
            "rapport.csv": {"data": b"csv", "mtime": 2, "kind": "report", "size": 20},
            "Week 40-2026.xls": {"data": b"xls", "mtime": 3, "kind": "planning"},
        }
        catalog = merge_file_catalog(previous, incoming, loaded_kinds={"report"})
        self.assertEqual(
            set(catalog),
            {"Week 39-2026.xls", "Week 40-2026.xls", "rapport.csv"},
        )
        only_new_week = merge_file_catalog(
            {"Week 39-2026.xls": {"mtime": 1, "kind": "planning", "size": 10}},
            {"Week 40-2026.xls": {"data": b"xls", "mtime": 3, "kind": "planning"}},
            loaded_kinds={"report"},
        )
        self.assertEqual(set(only_new_week), {"Week 39-2026.xls", "Week 40-2026.xls"})

    def test_parse_all_reports_keeps_every_file(self):
        header = "Trx ID;Trx Date;Truck PlateNr;Trx Country;Distance KM;Total Amt (EUR);Billing Status"
        uploaded = {
            "week39.csv": {
                "data": f"{header}\n1;21/09/2026;11-AAA-1;NL;10;5.00;Billed\n".encode(),
                "mtime": 1,
                "kind": "report",
            },
            "week40.csv": {
                "data": f"{header}\n2;28/09/2026;22-BBB-2;BE;8;3.50;Billed\n".encode(),
                "mtime": 2,
                "kind": "report",
            },
        }
        rows, names = parse_all_reports(uploaded)
        self.assertEqual(sorted(names), ["week39.csv", "week40.csv"])
        self.assertEqual({row["plate"] for row in rows}, {"11AAA1", "22BBB2"})
        self.assertAlmostEqual(sum(row["eur"] for row in rows), 8.5)

    def test_dmy_iso_accepts_separators(self):
        self.assertEqual(dmy_iso("22/09/2026"), "2026-09-22")
        self.assertEqual(dmy_iso("22-09-2026"), "2026-09-22")
        self.assertEqual(dmy_iso("2026-09-22"), "2026-09-22")
        self.assertEqual(dmy_iso("22.09.2026 14:36:43"), "2026-09-22")
    def test_dedupe_prefers_billed_over_dont_bill(self):
        rows = dedupe_report_rows(
            [
                trx(id="a1", billing="Don't Bill", eur=12.01, km=20),
                trx(id="b2", billing="Billed", eur=12.01, km=20),
            ]
        )
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["billing"], "Billed")
        self.assertEqual(rows[0]["eur"], 12.01)

    def test_german_summary_and_foreign_bundle_dropped(self):
        gantries = [
            trx(id=str(i), time=f"10:0{i}:00", road=f"A {i}", entry=f"A {i}", km=10, eur=2)
            for i in range(6)
        ]
        summary = trx(id="sum", time="10:09:00", entry="123456", km=60, eur=12)
        foreign = trx(id="at", time="10:10:00", entry="ASFINAG", km=320, eur=80, country="DE")
        kept = collapse_detail_summaries(gantries + [summary, foreign])
        ids = {row["id"] for row in kept}
        self.assertTrue({"0", "1", "2", "3", "4", "5"} <= ids)
        self.assertNotIn("sum", ids)
        self.assertNotIn("at", ids)
        self.assertAlmostEqual(sum(row["eur"] for row in kept), 12.0)

    def test_french_km_decimal_shift(self):
        self.assertEqual(_correct_french_km(12.45, 18.83, "FR"), 124.5)
        self.assertEqual(_correct_french_km(124.5, 18.83, "FR"), 124.5)

    def test_plan_km_is_per_country_not_trip_total(self):
        rows = [
            {"date": date(2026, 9, 23), "plate": "X", "customer": "ETC", "country": "België", "km": 40.0, "planKm": 760.0},
            {"date": date(2026, 9, 23), "plate": "X", "customer": "ETC", "country": "Frankrijk", "km": 343.2, "planKm": 760.0},
            {"date": date(2026, 9, 23), "plate": "X", "customer": "ETC", "country": "Nederland", "km": 20.0, "planKm": 760.0},
        ]
        _allocate_plan_km_by_country(rows)
        by_country = {row["country"]: row["planKm"] for row in rows}
        self.assertEqual(by_country["België"], 40.0)
        self.assertEqual(by_country["Nederland"], 20.0)
        self.assertEqual(by_country["Frankrijk"], 700.0)
        self.assertEqual(sum(by_country.values()), 760.0)

    def test_toll_is_assigned_to_only_one_week(self):
        def plan(week, day, ritnr):
            return {
                "week": week,
                "date": day,
                "plate": "X",
                "customer": "TNT",
                "km": 100,
                "tripNumber": ritnr,
                "plateRaw": "X",
            }

        index = _plan_index(
            [
                plan("Week 36-2026", "2026-09-01", "36a"),
                plan("Week 36-2026", "2026-09-04", "36b"),
                plan("Week 36-2026", "2026-09-06", "36c"),
                plan("Week 37-2026", "2026-09-03", "37a"),
                plan("Week 37-2026", "2026-09-07", "37b"),
                plan("Week 37-2026", "2026-09-08", "37c"),
            ]
        )
        plan36, exact36 = _assign_plan("2026-09-04", "X", index)
        self.assertTrue(exact36)
        self.assertEqual(plan36["week"], "Week 36-2026")
        plan37, exact37 = _assign_plan("2026-09-07", "X", index)
        self.assertTrue(exact37)
        self.assertEqual(plan37["week"], "Week 37-2026")
        nearest, nearest_exact = _assign_plan("2026-09-05", "X", index)
        self.assertFalse(nearest_exact)
        self.assertEqual(nearest["week"], "Week 36-2026")
        self.assertIs(_assign_plan("2026-09-05", "X", index)[0], nearest)

    def test_same_plate_other_day_in_week_still_matches(self):
        plan = {"date": "2026-09-21", "plate": "46BNT5", "customer": "Raben", "km": 181, "tripNumber": "677664"}
        found, exact = _resolve_plan(
            "2026-09-22",
            "46BNT5",
            {("2026-09-21", "46BNT5"): plan},
            {"46BNT5": [plan]},
            "2026-09-21",
            "2026-09-26",
        )
        self.assertIs(found, plan)
        self.assertFalse(exact)
        missing, _ = _resolve_plan(
            "2026-09-22",
            "21BSZ2",
            {("2026-09-21", "46BNT5"): plan},
            {"46BNT5": [plan]},
            "2026-09-21",
            "2026-09-26",
        )
        self.assertIsNone(missing)

    def test_incomplete_french_km_filled_from_rittenboek(self):
        rows = [
            {"country": "Frankrijk", "planKm": 700.0, "km": 343.2, "eur": 188.3},
        ]
        _fill_incomplete_french_km(rows)
        self.assertTrue(rows[0]["kmFromPlan"])
        self.assertEqual(rows[0]["km"], 700.0)
        self.assertEqual(rows[0]["kmReport"], 343.2)
        self.assertEqual(rows[0]["eur"], 188.3)

    def test_customer_pdf_has_read_guide_not_internal_notes(self):
        pdf = make_pdf_bytes(
            "ETC",
            [
                {
                    "date": date(2026, 9, 23),
                    "plate": "00-ABC-1",
                    "ritnr": "12",
                    "country": "Frankrijk",
                    "planKm": 700.0,
                    "km": 700.0,
                    "kmFromPlan": True,
                    "eur": 188.3,
                }
            ],
            {"id": "Week 39-2026", "from": "2026-09-21", "to": "2026-09-27", "date": ""},
            "Wekelijks_rapport_geheim.csv",
        )
        text = "".join(page.extract_text() or "" for page in PdfReader(BytesIO(pdf)).pages)
        self.assertIn("Leeswijzer", text)
        self.assertIn("KM rit", text)
        self.assertIn("planning", text.lower())
        self.assertIn("rittenboek", text.lower())
        self.assertNotIn("Don't Bill", text)
        self.assertNotIn("Gefactureerd", text)
        self.assertNotIn("Wekelijks_rapport_geheim.csv", text)
        self.assertNotIn("TISPL", text)
        self.assertNotIn("Telepass", text)


if __name__ == "__main__":
    unittest.main()
