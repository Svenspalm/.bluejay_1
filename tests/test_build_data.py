import json, os, sys, tempfile, unittest
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts"))
import build_data as B


def bill(num=None, typ="HB", status="(H) Introduced", chapter=None, sponsor=7, title="Test", draft="LC0001", ts="2027-01-05T10:00:00"):
    return {"billNumber": num, "billType": {"code": typ, "chamber": "HOUSE"} if num else None, "sponsorId": sponsor,
            "sessionLawChapterNumber": chapter,
            "draft": {"draftNumber": draft, "shortTitle": title, "date": "2026-10-01T00:00:00", "fiscalNote": True, "legalNote": False,
                      "subjects": [{"subjectCode": {"description": "Taxation"}}],
                      "billStatuses": [{"timeStamp": ts, "billStatusCode": {"name": status}}]}}


LEGS = [{"id": 7, "firstName": "Ada", "lastName": "Lovelace", "displayName": None, "politicalParty": {"code": "D"}}]


class Outcomes(unittest.TestCase):
    def test_rules(self):
        self.assertEqual(B.outcome_of(bill(1, chapter=5)), "Enacted")
        self.assertEqual(B.outcome_of(bill(1, status="(S) Veto Override Passed")), "Enacted")
        self.assertEqual(B.outcome_of(bill(1, status="Vetoed by Governor")), "Vetoed")
        self.assertEqual(B.outcome_of(bill(1, status="(H) Died in Standing Committee")), "Failed")
        self.assertEqual(B.outcome_of(bill(1, status="(H) Withdrawn")), "Withdrawn")
        self.assertEqual(B.outcome_of(bill(1)), "In progress")
        self.assertEqual(B.outcome_of(bill(None, status="(LC) Drafter Assigned")), "Draft request")


class Normalize(unittest.TestCase):
    def test_rows_sorted_and_shaped(self):
        raw = {"content": [bill(None, draft="LC0009"), bill(2, draft="LC0002"), bill(1, draft="LC0003")]}
        out = B.normalize(raw, LEGS, 2027)
        self.assertEqual((out["drafts"], out["numbered"]), (3, 2))
        self.assertEqual([r["bill"] for r in out["bills"]], ["HB1", "HB2", None])   # numbered first, drafts last
        r = out["bills"][0]
        self.assertEqual((r["sponsor"], r["party"], r["subjects"], r["fiscal_note"]), ("Ada Lovelace", "D", ["Taxation"], True))
        self.assertIn("history", r)
        self.assertNotIn("history", out["bills"][2])

    def test_write_if_changed(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "x", "bills.json")
            self.assertTrue(B.write_if_changed(p, {"a": 1}))
            self.assertFalse(B.write_if_changed(p, {"a": 1}))   # identical content: no rewrite, so no commit
            self.assertTrue(B.write_if_changed(p, {"a": 2}))

    def test_empty_response_refuses_to_overwrite(self):
        with self.assertRaises(SystemExit):
            B.build({"year": 2027, "id": 3, "live": True}, raw={"content": []}, legislators=[])


if __name__ == "__main__":
    unittest.main()
