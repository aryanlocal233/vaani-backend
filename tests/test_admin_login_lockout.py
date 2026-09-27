"""Unit tests for admin.py's in-process login lockout (added alongside the security hardening
pass: CORS lockdown, disabled public docs, https_only cookie, /ws + /admin/login nginx rate
limits). Exercises the lockout dict directly rather than through a real HTTP request/response
cycle, since the logic under test is the counting/expiry, not FastAPI routing."""
import admin


def setup_function():
    # The lockout store is module-level (safe because the app runs a single uvicorn worker) --
    # reset it before each test so tests don't leak state into each other.
    admin._failed_logins.clear()


def test_not_locked_out_initially():
    assert admin._is_locked_out("1.2.3.4:someadmin") is False


def test_locks_out_after_max_attempts():
    key = "1.2.3.4:someadmin"
    for _ in range(admin._LOGIN_MAX_ATTEMPTS):
        admin._record_failed_login(key)
    assert admin._is_locked_out(key) is True


def test_not_locked_out_below_max_attempts():
    key = "1.2.3.4:someadmin"
    for _ in range(admin._LOGIN_MAX_ATTEMPTS - 1):
        admin._record_failed_login(key)
    assert admin._is_locked_out(key) is False


def test_lockout_is_scoped_per_ip_and_username():
    # A brute-force attempt against one account from one IP must not lock out a different
    # account, or the same account from a legitimate IP.
    attacker_key = "9.9.9.9:admin"
    for _ in range(admin._LOGIN_MAX_ATTEMPTS):
        admin._record_failed_login(attacker_key)
    assert admin._is_locked_out("9.9.9.9:admin") is True
    assert admin._is_locked_out("9.9.9.9:other_admin") is False
    assert admin._is_locked_out("1.1.1.1:admin") is False


def test_old_attempts_outside_window_do_not_count(monkeypatch):
    key = "1.2.3.4:someadmin"
    times = iter([0.0, 0.0, 0.0, 0.0, 0.0])
    monkeypatch.setattr(admin.time, "monotonic", lambda: next(times, 0.0))
    for _ in range(admin._LOGIN_MAX_ATTEMPTS):
        admin._record_failed_login(key)

    # Now simulate time having moved past the lockout window -- old attempts should be pruned.
    monkeypatch.setattr(admin.time, "monotonic", lambda: admin._LOGIN_WINDOW_SECONDS + 100)
    assert admin._is_locked_out(key) is False
