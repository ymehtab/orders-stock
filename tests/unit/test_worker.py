from threading import Event
from unittest.mock import Mock, call, sentinel

import psycopg
import pytest

from orders_stock import worker


def test_drains_available_jobs_then_waits_when_idle(monkeypatch):
    stop = Event()
    wait = Mock(side_effect=lambda _: stop.set())
    monkeypatch.setattr(stop, "wait", wait)
    process = Mock(side_effect=["web-1", "web-2", None])
    monkeypatch.setattr(worker, "process_next_job", process)

    worker.run(sentinel.database, stop, poll_interval_seconds=0.25)

    assert process.call_args_list == [call(sentinel.database)] * 3
    wait.assert_called_once_with(0.25)


def test_database_failure_waits_and_retries(monkeypatch):
    stop = Event()
    wait = Mock()
    monkeypatch.setattr(stop, "wait", wait)

    def process_job(_):
        if process.call_count == 1:
            raise psycopg.OperationalError
        stop.set()
        return "web-1"

    process = Mock(side_effect=process_job)
    monkeypatch.setattr(worker, "process_next_job", process)

    worker.run(sentinel.database, stop, poll_interval_seconds=0.25)

    assert process.call_args_list == [call(sentinel.database)] * 2
    wait.assert_called_once_with(0.25)


def test_stop_request_prevents_processing(monkeypatch):
    stop = Event()
    stop.set()
    process = Mock()
    monkeypatch.setattr(worker, "process_next_job", process)

    worker.run(sentinel.database, stop, poll_interval_seconds=0.25)

    process.assert_not_called()


def test_programming_errors_are_not_silently_retried(monkeypatch):
    stop = Event()
    wait = Mock(side_effect=lambda _: stop.set())
    monkeypatch.setattr(stop, "wait", wait)
    monkeypatch.setattr(worker, "process_next_job", Mock(side_effect=ValueError))

    with pytest.raises(ValueError):
        worker.run(sentinel.database, stop, poll_interval_seconds=0.25)

    wait.assert_not_called()
