from datetime import datetime
from typing import Literal
from pydantic import BaseModel,Field
from enum import Enum
class JobStatus(str,Enum): QUEUED="queued"; DOWNLOADING="downloading"; RUNNING="running"; COMPLETED="completed"; FAILED="failed"
class JobSource(BaseModel): type:Literal["url","upload"]; filename:str|None=None; content_type:str|None=None; size:int|None=None; url:str|None=None
class Segment(BaseModel): start:float; end:float; text:str
class TranscriptionResult(BaseModel): language:str; text:str; segments:list[Segment]
class NotificationResult(BaseModel): type:Literal["callback"]="callback"; status:Literal["sent","failed"]; sent_at:datetime|None=None; error:str|None=None
class TranscriptionRequest(BaseModel):
    source:JobSource; model:Literal["small","medium"]="small"; language:str="auto"; debug:bool=False
    callback_url:str|None=None; callback_headers:dict[str,str]=Field(default_factory=dict)
class Base64TranscriptionRequest(BaseModel):
    filename:str|None=None; content_type:str|None=None; data:str; model:Literal["small","medium"]="small"; language:str="auto"; debug:bool=False
    callback_url:str|None=None; callback_headers:dict[str,str]=Field(default_factory=dict)
class Job(BaseModel):
    id:str; status:JobStatus; source:JobSource; model:Literal["small","medium"]; language:str; debug:bool=False; progress:float=0
    created_at:datetime; started_at:datetime|None=None; completed_at:datetime|None=None; transcribed_at:datetime|None=None
    audio_duration_seconds:float|None=None; transcription_duration_seconds:float|None=None; real_time_factor:float|None=None
    result:TranscriptionResult|None=None; notification:NotificationResult|None=None; error:str|None=None
    callback_url:str|None=Field(default=None,exclude=True); callback_headers:dict[str,str]=Field(default_factory=dict,exclude=True)
