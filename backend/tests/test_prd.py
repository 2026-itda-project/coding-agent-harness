import unittest

from app.prd import build_prd, draft_overview, validate_prd
from app.schemas import Gap, GapLink, Requirement, ReviewedRequirements

PID = "project-1"


def req(id, category, status="confirmed", ac=()):
    return Requirement(project_id=PID, id=id, category=category, description=f"{id} 내용", status=status,
                       source="initial_input", blocking=False, acceptance_criteria=list(ac))


def gap(id, related):
    return Gap(id=id, related_requirement_ids=related, category="exception_handling",
               description=f"{id} 미결정", blocking=False)


def reviewed(open_gaps=(), gap_links=()):
    return ReviewedRequirements(
        project_id=PID, run_id="run",
        requirements=[req("REQ-001", "role"), req("REQ-002", "functional", ac=["조회된다"]),
                      req("REQ-003", "nfr", status="needs_clarification")],
        open_gaps=list(open_gaps), gap_links=list(gap_links), metrics={})


def errors(prd, data):
    return validate_prd(prd, data)["errors"]


class OpenIssuesTest(unittest.TestCase):
    def test_open_gaps_become_open_issues_with_sources(self):
        data = reviewed([gap("GAP-001", ["REQ-002"]), gap("GAP-002", [])])
        prd = build_prd(data, draft_overview("서비스"))
        self.assertEqual([(i.id, i.description, i.source_requirement_ids, i.source_gap_ids) for i in prd.open_issues], [
            ("ISSUE-001", "REQ-003 내용", ["REQ-003"], []),
            ("ISSUE-002", "GAP-001 미결정", ["REQ-002"], ["GAP-001"]),
            ("ISSUE-003", "GAP-002 미결정", [], ["GAP-002"]),
        ])
        self.assertEqual(len(prd.functional_requirements), 1)  # Gap은 본문 Requirement가 되지 않는다
        self.assertTrue(validate_prd(prd, data)["passed"])

    def test_handoff_without_gaps_is_unchanged(self):
        prd = build_prd(reviewed(), draft_overview("서비스"))
        self.assertEqual([(i.source_requirement_ids, i.source_gap_ids) for i in prd.open_issues], [(["REQ-003"], [])])
        self.assertTrue(validate_prd(prd, reviewed())["passed"])

    def test_linked_gaps_trace_to_their_requirement_item(self):
        data = reviewed(gap_links=[GapLink(gap=gap("GAP-001", ["REQ-002"]), requirement_id="REQ-002"),
                                   GapLink(gap=gap("GAP-002", ["REQ-003"]), requirement_id="REQ-003")])
        prd = build_prd(data, draft_overview("서비스"))
        fr = prd.functional_requirements[0]
        self.assertEqual((fr.source_requirement_ids, fr.source_gap_ids), (["REQ-002"], ["GAP-001"]))
        # 미결정 Requirement에 연결된 Gap은 그 Open Issue 하나로 합쳐진다
        self.assertEqual([(i.description, i.source_requirement_ids, i.source_gap_ids) for i in prd.open_issues],
                         [("REQ-003 내용 / GAP-002 미결정", ["REQ-003"], ["GAP-002"])])
        self.assertEqual(fr.description, "REQ-002 내용")  # 확정 항목은 결정 문구만 둔다
        self.assertTrue(validate_prd(prd, data)["passed"])

    def test_validation_rejects_wrong_or_missing_gap_sources(self):
        data = reviewed([gap("GAP-001", ["REQ-002"])],
                        [GapLink(gap=gap("GAP-002", ["REQ-002"]), requirement_id="REQ-002")])
        prd = build_prd(data, draft_overview("서비스"))
        issue, fr = prd.open_issues[1], prd.functional_requirements[0]
        cases = {
            "unknown gap": (prd.model_copy(update={"open_issues": [prd.open_issues[0],
                                                                   issue.model_copy(update={"source_gap_ids": ["GAP-999"]})]}),
                            "ISSUE-002의 출처 Gap GAP-999가 인계 Gap과 맞지 않습니다."),
            "unknown requirement": (prd.model_copy(update={"open_issues": [prd.open_issues[0],
                                                                           issue.model_copy(update={"source_requirement_ids": ["REQ-999"]})]}),
                                    "ISSUE-002의 출처 Requirement가 인계물에 없습니다."),
            "open gap dropped": (prd.model_copy(update={"open_issues": [prd.open_issues[0]]}),
                                 "GAP-001가 PRD 항목 출처에 없습니다."),
            "linked gap dropped": (prd.model_copy(update={"functional_requirements": [fr.model_copy(update={"source_gap_ids": []})]}),
                                   "GAP-002가 PRD 항목 출처에 없습니다."),
            "linked gap on wrong item": (prd.model_copy(update={"open_issues": [prd.open_issues[0].model_copy(update={"source_gap_ids": ["GAP-002"]}), issue]}),
                                         "ISSUE-001의 출처 Gap GAP-002가 인계 Gap과 맞지 않습니다."),
        }
        for name, (broken, message) in cases.items():
            with self.subTest(name):
                self.assertIn(message, errors(broken, data))

    def test_validation_rejects_gap_both_open_and_linked(self):
        g = gap("GAP-001", ["REQ-002"])
        data = reviewed([g], [GapLink(gap=g, requirement_id="REQ-002")])
        self.assertIn("같은 Gap이 남은 Gap과 연결된 Gap에 함께 있습니다.", errors(build_prd(data, draft_overview("서비스")), data))


if __name__ == "__main__":
    unittest.main()
