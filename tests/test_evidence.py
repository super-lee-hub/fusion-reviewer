from __future__ import annotations

import pytest

# deepreview is now optional. Skip tests that depend on it.
pytestmark = pytest.mark.skip(reason="deepreview is now optional; PDF export tests require deepreview installed")


def test_export_pdf_report_skipped():
    """deepreview-dependent evidence tests will be re-enabled when deepreview is available."""
    pass
