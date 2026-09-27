import json,logging,threading,time
import av
import httpx
from mutagen import File as MutagenFile
from .models import JobStatus,NotificationResult
from .config import CALLBACK_TIMEOUT
from .security import validate_callback_url
from .source import download_url,validate_extension
log=logging.getLogger("speech.worker")
def duration(p):
    """Return audio duration in seconds using PyAV/FFmpeg, then Mutagen."""
    try:
        with av.open(str(p)) as container:
            if container.duration is not None:
                value=float(container.duration/av.time_base)
                if value>0:return value
            for stream in container.streams.audio:
                if stream.duration is not None and stream.time_base is not None:
                    value=float(stream.duration*stream.time_base)
                    if value>0:return value
    except Exception as e:
        log.debug("PyAV could not determine duration for %s: %s",p,e)
    try:
        a=MutagenFile(p)
        if a and a.info and a.info.length:return float(a.info.length)
    except Exception as e:
        log.debug("Mutagen could not determine duration for %s: %s",p,e)
    raise RuntimeError(f"Unable to determine audio duration: {p}")
class Worker:
    def __init__(self,m,t,d):self.m=m;self.t=t;self.d=d
    def start(self):threading.Thread(target=self.run,daemon=True,name="stt-worker").start()
    def run(self):
        while True:
            i=self.m.queue.get()
            try:self.process(i)
            except Exception:log.exception("Unhandled job %s",i)
            finally:self.m.queue.task_done()
    def process(self,i):
        j=self.m.get(i)
        if not j:return
        try:
            p=self.prepare(j);j.status=JobStatus.RUNNING;j.started_at=self.m.now();self.m.update(j)
            try:
                j.audio_duration_seconds=duration(p)
            except Exception as e:
                log.warning("Unable to determine audio duration for %s: %s",p,e)
                j.audio_duration_seconds=None
            self.m.update(j)
            t=time.perf_counter();r=self.t.transcribe(str(p),j.model,j.language);elapsed=time.perf_counter()-t
            if not r.text.strip():raise RuntimeError("Empty transcription")
            j.transcription_duration_seconds=elapsed;j.real_time_factor=elapsed/j.audio_duration_seconds if j.audio_duration_seconds else None
            j.result=r;j.transcribed_at=self.m.now();j.status=JobStatus.COMPLETED;j.progress=1;j.completed_at=self.m.now();self.m.update(j);self.notify(j)
        except Exception as e:
            log.exception("Job %s failed",i);j.status=JobStatus.FAILED;j.error=str(e);j.completed_at=self.m.now();self.m.update(j);self.notify(j)
    def prepare(self,j):
        d=self.d/j.id;d.mkdir(parents=True,exist_ok=True)
        if j.source.type=="upload":
            p=d/j.source.filename
            if not p.exists():raise RuntimeError("Audio file not found")
            validate_extension(j.source.filename);return p
        meta=d/"source.json"
        if not meta.exists():raise RuntimeError("Missing URL source metadata")
        j.status=JobStatus.DOWNLOADING;self.m.update(j)
        n,c,s=download_url(json.loads(meta.read_text())["url"],d);j.source.filename=n;j.source.content_type=c;j.source.size=s;self.m.update(j);return d/n
    def notify(self,j):
        if not j.callback_url:return
        try:
            validate_callback_url(j.callback_url);payload=j.model_dump(mode="json",exclude={"callback_url","callback_headers"})
            with httpx.Client(timeout=CALLBACK_TIMEOUT) as c:r=c.post(j.callback_url,json=payload,headers={"Content-Type":"application/json",**j.callback_headers});r.raise_for_status()
            j.notification=NotificationResult(status="sent",sent_at=self.m.now())
        except Exception as e:j.notification=NotificationResult(status="failed",error=str(e))
        self.m.update(j)
