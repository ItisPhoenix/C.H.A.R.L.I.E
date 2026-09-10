import ctypes
import sys

import pytest

import charlie.terminal_service as terminal_service

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="Windows ConPTY only")


def _value(handle):
    return getattr(handle, "value", handle)


class _FakeKernel32:
    def __init__(self, *, fail=None):
        self.fail = fail
        self.calls = []
        self.closed_handles = []
        self.job_flags = None
        self.job_handle = 900
        self.process_handle = 100
        self.thread_handle = 101
        self.pid = 4242
        self.terminated_processes = []

    @staticmethod
    def _set_handle(pointer, value, handle_type):
        ctypes.cast(pointer, ctypes.POINTER(handle_type)).contents.value = value

    def CreatePipe(self, read_pointer, write_pointer, *_args):
        value = 200 + len([call for call in self.calls if call[0] == "CreatePipe"])
        self._set_handle(read_pointer, value, terminal_service.wintypes.HANDLE)
        self._set_handle(write_pointer, value + 100, terminal_service.wintypes.HANDLE)
        self.calls.append(("CreatePipe",))
        return True

    def CloseHandle(self, handle):
        self.closed_handles.append(_value(handle))
        self.calls.append(("CloseHandle", _value(handle)))
        return True

    def CreatePseudoConsole(self, *_args):
        self._set_handle(_args[-1], 300, ctypes.c_void_p)
        self.calls.append(("CreatePseudoConsole",))
        return 0

    def InitializeProcThreadAttributeList(self, attribute_list, _count, _flags, size_pointer):
        if not attribute_list:
            ctypes.cast(size_pointer, ctypes.POINTER(ctypes.c_size_t)).contents.value = 128
        self.calls.append(("InitializeProcThreadAttributeList", bool(attribute_list)))
        return True

    def UpdateProcThreadAttribute(self, *_args):
        self.calls.append(("UpdateProcThreadAttribute",))
        return True

    def DeleteProcThreadAttributeList(self, *_args):
        self.calls.append(("DeleteProcThreadAttributeList",))
        return True

    def CreateJobObjectW(self, *_args):
        self.calls.append(("CreateJobObjectW",))
        if self.fail == "create_job":
            return terminal_service.wintypes.HANDLE()
        return terminal_service.wintypes.HANDLE(self.job_handle)

    def SetInformationJobObject(self, _job, _kind, info_pointer, _size):
        self.calls.append(("SetInformationJobObject",))
        if self.fail == "set_job":
            return False
        info = ctypes.cast(
            info_pointer,
            ctypes.POINTER(terminal_service.JOBOBJECT_EXTENDED_LIMIT_INFORMATION),
        ).contents
        self.job_flags = info.BasicLimitInformation.LimitFlags
        return True

    def CreateProcessW(self, _app, _cmd, _proc_attr, _thread_attr, _inherit, flags, _env, _cwd, _startup, pi_pointer):
        self.calls.append(("CreateProcessW", flags))
        if self.fail == "create_process":
            return False
        pi = ctypes.cast(pi_pointer, ctypes.POINTER(terminal_service.PROCESS_INFORMATION)).contents
        pi.hProcess = terminal_service.wintypes.HANDLE(self.process_handle)
        pi.hThread = terminal_service.wintypes.HANDLE(self.thread_handle)
        pi.dwProcessId = self.pid
        pi.dwThreadId = 4343
        return True

    def AssignProcessToJobObject(self, _job, _process):
        self.calls.append(("AssignProcessToJobObject",))
        return self.fail != "assign_job"

    def ResumeThread(self, _thread):
        self.calls.append(("ResumeThread",))
        return 0xFFFFFFFF if self.fail == "resume" else 1

    def TerminateJobObject(self, _job, _code):
        self.calls.append(("TerminateJobObject",))
        return True

    def TerminateProcess(self, process, _code):
        self.calls.append(("TerminateProcess", _value(process)))
        self.terminated_processes.append(_value(process))
        return True

    def WaitForSingleObject(self, *_args):
        self.calls.append(("WaitForSingleObject",))
        return 0


def _start_with_fake(monkeypatch, fake):
    monkeypatch.setattr(terminal_service, "kernel32", fake)
    backend = terminal_service.WindowsConPTY(cwd="C:\\")
    backend.start()
    return backend


def test_conpty_assigns_suspended_root_to_job_before_resume(monkeypatch):
    fake = _FakeKernel32()
    backend = _start_with_fake(monkeypatch, fake)
    try:
        process_flags = next(call[1] for call in fake.calls if call[0] == "CreateProcessW")
        names = [call[0] for call in fake.calls]
        assert process_flags == terminal_service.EXTENDED_STARTUPINFO_PRESENT | terminal_service.CREATE_SUSPENDED
        assert names.index("SetInformationJobObject") < names.index("CreateProcessW")
        assert names.index("AssignProcessToJobObject") < names.index("ResumeThread")
        assert fake.job_flags == terminal_service.JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        assert not fake.job_flags & terminal_service.JOB_OBJECT_LIMIT_BREAKAWAY_OK
        assert not fake.job_flags & terminal_service.JOB_OBJECT_LIMIT_SILENT_BREAKAWAY_OK
        assert not process_flags & terminal_service.CREATE_BREAKAWAY_FROM_JOB
    finally:
        backend.close()


@pytest.mark.parametrize("failure", ["create_job", "set_job", "assign_job", "resume"])
def test_conpty_job_setup_failures_fail_closed(monkeypatch, failure):
    fake = _FakeKernel32(fail=failure)
    monkeypatch.setattr(terminal_service, "kernel32", fake)
    backend = terminal_service.WindowsConPTY(cwd="C:\\")

    with pytest.raises(OSError):
        backend.start()

    names = [call[0] for call in fake.calls]
    assert backend._closed is True
    assert not _value(backend._h_job)
    assert not _value(backend._pi.hProcess)
    assert "ResumeThread" not in names if failure in {"create_job", "set_job", "assign_job"} else True
    if failure in {"assign_job", "resume"}:
        assert "TerminateJobObject" in names
        assert (fake.process_handle in fake.closed_handles) or (fake.thread_handle in fake.closed_handles)
    if failure == "assign_job":
        assert fake.terminated_processes == [fake.process_handle]
        terminate_index = next(index for index, call in enumerate(fake.calls) if call[0] == "TerminateProcess")
        process_close_index = fake.calls.index(("CloseHandle", fake.process_handle))
        assert terminate_index < process_close_index
        assert "ResumeThread" not in names
    if failure == "resume":
        assert fake.terminated_processes == []


def test_conpty_close_terminates_job_and_is_idempotent(monkeypatch):
    fake = _FakeKernel32()
    backend = _start_with_fake(monkeypatch, fake)

    backend.close()
    first_terminate_count = sum(call[0] == "TerminateJobObject" for call in fake.calls)
    first_job_close_count = fake.closed_handles.count(fake.job_handle)
    backend.close()

    assert first_terminate_count == 1
    assert first_job_close_count == 1
    assert sum(call[0] == "TerminateJobObject" for call in fake.calls) == 1
    assert fake.terminated_processes == []
    assert fake.closed_handles.count(fake.job_handle) == 1
    assert not _value(backend._h_job)


def test_conpty_job_configuration_has_no_breakaway_flags(monkeypatch):
    fake = _FakeKernel32()
    backend = _start_with_fake(monkeypatch, fake)
    try:
        assert fake.job_flags == terminal_service.JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        assert fake.job_flags & (
            terminal_service.JOB_OBJECT_LIMIT_BREAKAWAY_OK
            | terminal_service.JOB_OBJECT_LIMIT_SILENT_BREAKAWAY_OK
        ) == 0
    finally:
        backend.close()
