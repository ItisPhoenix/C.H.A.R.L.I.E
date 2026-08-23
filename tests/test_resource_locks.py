from charlie import resource_locks


def test_default_capability_lease_manager_is_introspectable() -> None:
    assert resource_locks.get_capability_lease_manager() is resource_locks.default_lease_manager


def test_acquire_succeeds_when_capability_is_free():
    assert resource_locks.acquire("desktop", "task-a") is True
    resource_locks.release("desktop", "task-a")


def test_acquire_fails_for_a_different_owner_while_held():
    assert resource_locks.acquire("desktop", "task-a") is True
    assert resource_locks.acquire("desktop", "task-b") is False
    resource_locks.release("desktop", "task-a")


def test_acquire_is_reentrant_for_the_same_owner():
    assert resource_locks.acquire("desktop", "task-a") is True
    assert resource_locks.acquire("desktop", "task-a") is True
    resource_locks.release("desktop", "task-a")


def test_release_frees_the_capability_for_another_owner():
    resource_locks.acquire("desktop", "task-a")
    resource_locks.release("desktop", "task-a")
    assert resource_locks.acquire("desktop", "task-b") is True
    resource_locks.release("desktop", "task-b")


def test_release_by_non_owner_is_a_no_op():
    resource_locks.acquire("desktop", "task-a")
    resource_locks.release("desktop", "task-b")
    assert resource_locks.current_owner("desktop") == "task-a"
    resource_locks.release("desktop", "task-a")


def test_capabilities_are_independent():
    assert resource_locks.acquire("desktop", "task-a") is True
    assert resource_locks.acquire("browser", "task-b") is True
    resource_locks.release("desktop", "task-a")
    resource_locks.release("browser", "task-b")


def test_current_owner_is_none_when_free():
    assert resource_locks.current_owner("nothing-holds-this") is None
