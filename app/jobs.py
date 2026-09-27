from pathlib import Path
from threading import Lock
from uuid import uuid4
import queue
from .models import Job
class JobManager:
    def __init__(self,d): self.d=d; d.mkdir(parents=True,exist_ok=True); self.jobs={}; self.queue=queue.Queue(); self.lock=Lock()
    @staticmethod
    def now():
        from datetime import datetime,timezone
        return datetime.now(timezone.utc)
    def new_id(self): return "tr_"+uuid4().hex[:16]
    def create(self,j):
        with self.lock:self.jobs[j.id]=j;self.save(j)
        self.queue.put(j.id);return j
    def get(self,i):
        with self.lock:
            if i in self.jobs:return self.jobs[i]
        p=self.d/f"{i}.json"
        if not p.exists():return None
        j=Job.model_validate_json(p.read_text(encoding="utf-8"))
        with self.lock:self.jobs[i]=j
        return j
    def update(self,j):
        with self.lock:self.jobs[j.id]=j;self.save(j)
    def save(self,j):
        p=self.d/f"{j.id}.json";t=p.with_suffix(".tmp");t.write_text(j.model_dump_json(indent=2,exclude={"callback_url","callback_headers"}),encoding="utf-8");t.replace(p)
    def delete(self,i):
        with self.lock: e=self.jobs.pop(i,None) is not None
        p=self.d/f"{i}.json"
        if p.exists():p.unlink();e=True
        return e
