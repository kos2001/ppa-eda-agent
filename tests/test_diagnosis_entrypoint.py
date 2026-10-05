from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]


class DiagnosisEntrypointTests(unittest.TestCase):
    def test_page_can_start_a_diagnosis_from_pasted_report_text(self):
        page = (ROOT / "dashboard/src/components/DiagnosisPage.tsx").read_text(encoding="utf-8")
        self.assertIn("runDiagnosis(reportText)", page)
        self.assertIn("disabled={!isReady || diagnosing || !reportText.trim()}", page)
        self.assertIn("onChange={(event) => setReportText(event.target.value)}", page)

    def test_provider_rejects_a_second_concurrent_diagnosis(self):
        provider = (ROOT / "dashboard/src/agentContext.tsx").read_text(encoding="utf-8")
        self.assertIn("if (diagnosing || (!serverConfigured && !key)) return;", provider)
