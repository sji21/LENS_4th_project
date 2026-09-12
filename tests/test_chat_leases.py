"""Lease recovery and fencing without external model calls."""
import uuid
from datetime import timedelta
from unittest.mock import Mock

import pytest
from django.db import DatabaseError
from django.utils import timezone

from chat import leases
from chat.models import Conversation

pytestmark = pytest.mark.django_db


def conversation():
    return Conversation.objects.create(
        expires_at=timezone.now() + timedelta(hours=1),
        busy_until=timezone.now() + timedelta(seconds=5),
        lease_token=uuid.uuid4(),
    )


def test_active_owner_renews_short_lease(settings):
    row = conversation()
    before = row.busy_until
    assert leases.renew(row.pk, row.lease_token) == 1
    row.refresh_from_db()
    assert before < row.busy_until <= timezone.now() + timedelta(seconds=30)
    assert settings.CHAT_LEASE_SECONDS == 30


def test_expired_owner_cannot_resurrect_lease():
    row = conversation()
    Conversation.objects.filter(pk=row.pk).update(busy_until=timezone.now() - timedelta(seconds=1))
    assert leases.renew(row.pk, row.lease_token) == 0


def test_old_owner_cannot_renew_successor():
    row = conversation()
    token = row.lease_token
    successor = uuid.uuid4()
    Conversation.objects.filter(pk=row.pk).update(lease_token=successor)
    assert leases.renew(row.pk, token) == 0
    row.refresh_from_db()
    assert row.lease_token == successor


def test_heartbeat_worker_renews_and_closes_connection(monkeypatch):
    event = Mock()
    event.wait.side_effect = [False, True]
    monkeypatch.setattr(leases, "Event", lambda: event)
    renew = Mock(return_value=1)
    monkeypatch.setattr(leases, "renew", renew)
    close = Mock()
    monkeypatch.setattr(leases.connections, "close_all", close)
    with leases.heartbeat(1, "token"):
        pass
    renew.assert_called_once_with(1, "token")
    event.set.assert_called_once()
    close.assert_called_once()


@pytest.mark.parametrize("outcomes, calls", [([0], 1), ([DatabaseError(), 1], 2)])
def test_worker_stops_on_lost_lease_and_retries_database_failure(monkeypatch, outcomes, calls):
    event = Mock()
    event.wait.side_effect = [False, False, True]
    monkeypatch.setattr(leases, "Event", lambda: event)
    renew = Mock(side_effect=outcomes)
    monkeypatch.setattr(leases, "renew", renew)
    close = Mock()
    monkeypatch.setattr(leases.connections, "close_all", close)
    with leases.heartbeat(1, "token"):
        pass
    assert renew.call_count == calls
    assert close.called
    event.set.assert_called_once()


def test_heartbeat_stops_when_request_raises(monkeypatch):
    event = Mock()
    event.wait.return_value = True
    monkeypatch.setattr(leases, "Event", lambda: event)
    with pytest.raises(RuntimeError):
        with leases.heartbeat(1, "token"):
            raise RuntimeError("request failed")
    event.set.assert_called_once()
