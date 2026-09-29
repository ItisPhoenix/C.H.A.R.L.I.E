from charlie import router


def test_natural_background_work_request_is_deterministic():
    query = (
        "Charlie, please check the Python version and the first heading in Windows "
        "taskkill help in the background, then message me here when you're done."
    )

    assert router.is_explicit_background_task_start(query) is True


def test_negated_background_work_request_is_not_started():
    assert router.is_explicit_background_task_start(
        "Please don't check anything in the background."
    ) is False
