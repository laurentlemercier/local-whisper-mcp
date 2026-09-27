import base64,binascii,mimetypes,re
from pathlib import Path
from urllib.parse import unquote,urlparse,urljoin
import httpx
from .config import MAX_INPUT_SIZE,HTTP_TIMEOUT
from .security import validate_source_url
SUPPORTED_EXTENSIONS={".m4a",".mp3",".wav",".ogg",".opus",".webm",".flac"}
def safe_filename(f):
    return re.sub(r"[^a-zA-Z0-9._-]","_",Path(f or "audio").name) or "audio"
def extension_from_content_type(c):
    return mimetypes.guess_extension(c.split(";",1)[0].strip().lower()) if c else None
def validate_extension(f):
    e=Path(f).suffix.lower()
    if e not in SUPPORTED_EXTENSIONS: raise ValueError(f"Unsupported audio format: {e or '(none)'}")
def filename_from_url(u,c):
    n=safe_filename(unquote(Path(urlparse(u).path).name))
    if n=="audio" or not Path(n).suffix:
        e=extension_from_content_type(c)
        if e:n+=e
    return n
def decode_base64_data(data,destination,filename=None,content_type=None):
    if data.startswith("data:"):
        try: h,data=data.split(",",1)
        except ValueError as e: raise ValueError("Invalid data URL") from e
        content_type=content_type or h[5:].split(";",1)[0]
    try: raw=base64.b64decode(data,validate=True)
    except (binascii.Error,ValueError) as e: raise ValueError("Invalid Base64 data") from e
    if len(raw)>MAX_INPUT_SIZE: raise ValueError("Input exceeds maximum size")
    name=safe_filename(filename)
    if name=="audio":
        e=extension_from_content_type(content_type)
        if not e: raise ValueError("filename or content_type is required")
        name+=e
    validate_extension(name); destination.mkdir(parents=True,exist_ok=True); (destination/name).write_bytes(raw)
    return name,len(raw)
def download_url(url,destination):
    current=url
    with httpx.Client(follow_redirects=False,timeout=HTTP_TIMEOUT) as c:
        for _ in range(6):
            validate_source_url(current); r=c.get(current)
            if r.status_code not in {301,302,303,307,308}: r.raise_for_status(); break
            loc=r.headers.get("location")
            if not loc: raise ValueError("Redirect without Location header")
            current=urljoin(current,loc)
        else: raise ValueError("Too many redirects")
        ct=r.headers.get("content-type"); cl=r.headers.get("content-length")
        if cl and int(cl)>MAX_INPUT_SIZE: raise ValueError("Remote file exceeds maximum size")
        name=filename_from_url(str(r.url),ct); validate_extension(name); raw=r.content
        if len(raw)>MAX_INPUT_SIZE: raise ValueError("Remote file exceeds maximum size")
        (destination/name).write_bytes(raw); return name,ct,len(raw)
