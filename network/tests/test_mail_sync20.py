"""Regression cases use local fakes only; no provider account or production DB."""
import threading
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
import pytest
from network.mailbox import Mailbox,SecureIMAP
from network.database import NotFound


def mailbox(monkeypatch):
    mail=Mailbox(SimpleNamespace())
    def account(owner,ident):
        if owner!='owner':raise NotFound('Недоступно')
        return {'id':ident}
    monkeypatch.setattr(mail,'account',account)
    return mail


def test_concurrent_manual_background_sync_is_coalesced_and_ownership_checked(monkeypatch):
    mail=mailbox(monkeypatch);entered=threading.Event();release=threading.Event()
    def sync(*args):
        entered.set();assert release.wait(3)
        return {'loaded':2}
    monkeypatch.setattr(mail,'_sync',sync)
    with ThreadPoolExecutor(max_workers=1) as pool:
        job=pool.submit(mail.sync,'owner','account')
        try:
            assert entered.wait(3)
            assert mail.sync('owner','account')=={'syncing':True,'loaded':0,'folder':'INBOX'}
            with pytest.raises(NotFound):mail.sync('intruder','account')
        finally:release.set()
        assert job.result()=={'loaded':2}
    monkeypatch.setattr(mail,'_sync',lambda *a:{'loaded':0})
    assert mail.sync('owner','account')=={'loaded':0}


def test_unexpected_failure_releases_sync_lock(monkeypatch):
    mail=mailbox(monkeypatch)
    def broken(*args):raise RuntimeError('local failure')
    monkeypatch.setattr(mail,'_sync',broken)
    with pytest.raises(RuntimeError):mail.sync('owner','account')
    assert mail.operation_lock('account').acquire(blocking=False)
    mail.operation_lock('account').release()


def test_different_accounts_never_share_a_lock(monkeypatch):
    mail=mailbox(monkeypatch)
    assert mail.operation_lock('one') is mail.operation_lock('one')
    assert len({id(mail.operation_lock(str(n))) for n in range(200)})==200


def test_imap_read_has_an_absolute_deadline(monkeypatch):
    import network.mailbox as module
    client=object.__new__(SecureIMAP);client.deadline=100;client.sock=SimpleNamespace(settimeout=lambda t:None)
    monkeypatch.setattr(module.time,'monotonic',lambda:101)
    with pytest.raises(TimeoutError):client._read_budget()


def test_imap_drip_feed_does_not_reset_total_deadline(monkeypatch):
    import network.mailbox as module
    client=object.__new__(SecureIMAP);client.deadline=45;client._read_buffer=bytearray()
    clock=[0];client.sock=SimpleNamespace(settimeout=lambda t:None)
    def read1(size):clock[0]+=10;return b'x'
    client.file=SimpleNamespace(read1=read1)
    monkeypatch.setattr(module.time,'monotonic',lambda:clock[0])
    with pytest.raises(TimeoutError):client.readline()
    assert clock[0]<=50


def test_imap_line_literal_and_next_line_preserve_buffer(monkeypatch):
    import network.mailbox as module
    client=object.__new__(SecureIMAP);client.deadline=100;client._read_buffer=bytearray()
    client.sock=SimpleNamespace(settimeout=lambda t:None)
    chunks=iter([b'* FETCH {4}\r\nDATA)\r\n',b''])
    client.file=SimpleNamespace(read1=lambda size:next(chunks))
    monkeypatch.setattr(module.time,'monotonic',lambda:0)
    assert client.readline()==b'* FETCH {4}\r\n'
    assert client.read(4)==b'DATA'
    assert client.readline()==b')\r\n'
