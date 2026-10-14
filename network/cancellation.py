"""Cancellation context propagated to source requests in each worker thread."""
import threading
from contextlib import contextmanager
local=threading.local()
class SearchCancelled(Exception):pass
def check():
 event=getattr(local,'event',None)
 if event and event.is_set():raise SearchCancelled()
def wait(seconds):
 event=getattr(local,'event',None)
 if event:
  if event.wait(max(0,seconds)):raise SearchCancelled()
 else:
  threading.Event().wait(max(0,seconds))
@contextmanager
def scope(event):
 previous=getattr(local,'event',None);local.event=event
 try:
  check();yield
 finally:local.event=previous
