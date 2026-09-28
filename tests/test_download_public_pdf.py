from charlie.capabilities import capability_index
from charlie.security.policy import check_tool_call
from charlie.tools import registry


def test_download_public_pdf_is_registered_and_uses_path_policy():
    assert "download_public_pdf" in registry.get_tool_names()

    operation = capability_index.get_operation("download_public_pdf")
    assert operation is not None
    assert operation.id == "file.public_pdf.download"
    assert operation.risk_class == "reversible"

    decision = check_tool_call("download_public_pdf", {"path": ".env"})
    assert decision.needs_approval
