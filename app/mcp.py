from mcp.server.fastmcp import FastMCP
from .main import manager,create_job
from .models import JobSource
from .config import API_TOKEN,INPUT_DIR
from .source import decode_base64_data
from .security import validate_source_url
import json,shutil
mcp = FastMCP(
    "local-whisper-speech-to-text",
    streamable_http_path="/",
)
def check(token):
    if API_TOKEN and token!=API_TOKEN:raise PermissionError("Invalid MCP token")
@mcp.tool()
def transcribe_url(url:str,model:str="small",language:str="auto",debug:bool=False,callback_url:str|None=None,callback_authorization:str|None=None,api_token:str|None=None)->dict:
    """Submit an audio URL for asynchronous transcription."""
    check(api_token);jid=manager.new_id();d=INPUT_DIR/jid;d.mkdir(parents=True,exist_ok=True)
    try:
        validate_source_url(url);(d/"source.json").write_text(json.dumps({"url":url}),encoding="utf-8")
        return create_job(JobSource(type="url",url=url),model,language,debug,callback_url,{"Authorization":callback_authorization} if callback_authorization else {},jid).model_dump(mode="json")
    except Exception:
        shutil.rmtree(d,ignore_errors=True);raise
@mcp.tool()
def transcribe_data(data_base64:str,filename:str,content_type:str|None=None,model:str="small",language:str="auto",debug:bool=False,callback_url:str|None=None,callback_authorization:str|None=None,api_token:str|None=None)->dict:
    """Submit Base64 audio for asynchronous transcription."""
    check(api_token);jid=manager.new_id();d=INPUT_DIR/jid;d.mkdir(parents=True,exist_ok=True)
    try:
        n,s=decode_base64_data(data_base64,d,filename,content_type)
        return create_job(JobSource(type="upload",filename=n,content_type=content_type,size=s),model,language,debug,callback_url,{"Authorization":callback_authorization} if callback_authorization else {},jid).model_dump(mode="json")
    except Exception:
        shutil.rmtree(d,ignore_errors=True);raise
@mcp.tool()
def get_transcription_status(job_id:str,api_token:str|None=None)->dict:
    """Get asynchronous transcription status."""
    check(api_token);j=manager.get(job_id);return j.model_dump(mode="json") if j else {"id":job_id,"error":"Job not found"}
@mcp.tool()
def get_transcription_result(job_id:str,api_token:str|None=None)->dict:
    """Get transcript and timing metrics."""
    check(api_token);j=manager.get(job_id)
    if not j:return {"id":job_id,"error":"Job not found"}
    return {"id":job_id,"status":j.status.value,"audio_duration_seconds":j.audio_duration_seconds,"transcription_duration_seconds":j.transcription_duration_seconds,"real_time_factor":j.real_time_factor,"result":j.result.model_dump(mode="json") if j.result else None,"error":j.error}
@mcp.tool()
def delete_transcription(job_id:str,api_token:str|None=None)->dict:
    """Delete a transcription job."""
    check(api_token)
    if not manager.get(job_id):return {"id":job_id,"deleted":False}
    e=manager.delete(job_id);shutil.rmtree(INPUT_DIR/job_id,ignore_errors=True);return {"id":job_id,"deleted":e}
def mount_mcp(app):app.mount("/mcp",mcp.streamable_http_app());return app
