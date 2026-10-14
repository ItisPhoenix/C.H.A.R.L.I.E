from types import SimpleNamespace

from charlie.core import _verify_notepad_save_action
from charlie.turn_contracts import ResultStatus, VerificationStatus


def _result(text):
    return SimpleNamespace(status=ResultStatus.COMPLETED.value, result=text)


def _save_history(path):
    return [
        (
            {"name": "desktop_type", "arguments": {"text": "Charlie is a local assistant."}},
            _result("Typed into Notepad; fresh UIA readback matches requested text."),
        ),
        (
            {"name": "desktop_windows", "arguments": {}},
            _result("Save As\nNotepad"),
        ),
        (
            {"name": "desktop_type", "arguments": {"text": str(path)}},
            _result("Typed the requested file name."),
        ),
        (
            {"name": "desktop_observe", "arguments": {}},
            _result('[7] Button "Save"'),
        ),
    ]


def test_notepad_save_is_verified_only_after_file_text_readback(tmp_path):
    path = tmp_path / "About Charlie.txt"
    path.write_text("Charlie is a local assistant.", encoding="utf-8")
    envelope = SimpleNamespace(
        result="Executed but not verified.",
        status=ResultStatus.UNVERIFIED.value,
        verification_status=VerificationStatus.EXECUTED_UNVERIFIED.value,
        verification=None,
        data={},
    )

    verified = _verify_notepad_save_action(
        "Open Notepad, write about yourself, and save it on the desktop.",
        {"mark_id": 7},
        _save_history(path),
        envelope,
    )

    assert verified is True
    assert envelope.status == ResultStatus.COMPLETED.value
    assert envelope.verification_status == VerificationStatus.VERIFIED_SUCCESS.value
    assert "matches what was entered" in envelope.result


def test_notepad_save_remains_unverified_when_readback_differs(tmp_path):
    path = tmp_path / "About Charlie.txt"
    path.write_text("Different content", encoding="utf-8")
    envelope = SimpleNamespace(
        result="Executed but not verified.",
        status=ResultStatus.UNVERIFIED.value,
        verification_status=VerificationStatus.EXECUTED_UNVERIFIED.value,
        verification=None,
        data={},
    )

    verified = _verify_notepad_save_action(
        "Open Notepad, write about yourself, and save it on the desktop.",
        {"mark_id": 7},
        _save_history(path),
        envelope,
    )

    assert verified is False
    assert envelope.status == ResultStatus.UNVERIFIED.value
    assert envelope.verification_status == VerificationStatus.EXECUTED_UNVERIFIED.value
