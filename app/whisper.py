import logging
from threading import Lock
from faster_whisper import WhisperModel
from .config import WHISPER_DEVICE,WHISPER_COMPUTE_TYPE
from .models import Segment,TranscriptionResult
log=logging.getLogger("speech.whisper")
class ModelManager:
    def __init__(self): self.models={}; self.lock=Lock()
    def get(self,name):
        if name not in {"small","medium"}: raise ValueError(f"Unsupported model: {name}")
        with self.lock:
            if name not in self.models:
                log.info("Loading model %s on %s (%s)",name,WHISPER_DEVICE,WHISPER_COMPUTE_TYPE)
                self.models[name]=WhisperModel(name,device=WHISPER_DEVICE,compute_type=WHISPER_COMPUTE_TYPE)
            return self.models[name]
    def loaded_models(self): return list(self.models)
class Transcriber:
    def __init__(self,m): self.m=m
    def transcribe(self,path,name,language):
        segs,info=self.m.get(name).transcribe(path,language=None if language=="auto" else language)
        out=[]; texts=[]
        for s in segs:
            t=s.text.strip()
            if t: texts.append(t); out.append(Segment(start=s.start,end=s.end,text=t))
        return TranscriptionResult(language=info.language,text=" ".join(texts),segments=out)
