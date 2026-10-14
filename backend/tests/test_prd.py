import unittest

from app.prd import build_prd, draft_overview, validate_prd
from app.schemas import Gap, Requirement, ReviewedRequirements

PID = "project-1"


def req(id, category, status="confirmed", ac=()):
    return Requirement(project_id=PID, id=id, category=category, description=f"{id} 내용", status=status,
                       source="initial_input", blocking=False, acceptance_criteria=list(ac))


def reviewed(open_gaps=()):
    return ReviewedRequirements(
        project_id=PID, run_id="run",
        requirements=[req("REQ-001", "role"), req("REQ-002", "functional", ac=["조회된다"]),
                      req("REQ-003", "nfr", status="needs_clarification")],
        open_gaps=list(open_gaps), metrics={})


def gap(id, related):
    return Gap(id=id, related_requirement_ids=related, category="exception_handling",
               description=f"{id} 미결정", blocking=False)


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
        self.assertEqual([i.source_requirement_ids for i in prd.open_issues], [["REQ-003"]])
        self.assertTrue(validate_prd(prd, reviewed())["passed"])

    def test_unknown_open_issue_source_fails_validation(self):
        data = reviewed([gap("GAP-001", ["REQ-002"])])
        prd = build_prd(data, draft_overview("서비스"))
        for source in ({"source_gap_ids": ["GAP-999"]}, {"source_requirement_ids": ["REQ-999"]}):
            broken = prd.model_copy(update={"open_issues": [prd.open_issues[1].model_copy(update=source)]})
            with self.subTest(source=source):
                result = validate_prd(broken, data)
                self.assertFalse(result["passed"])
                self.assertIn("ISSUE-002의 출처 ID가 인계 Requirement·Gap에 없습니다.", result["errors"])


if __name__ == "__main__":
    unittest.main()
