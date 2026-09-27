from pathlib import Path
import os
BASE_DIR=Path(os.getenv("DATA_DIR","./data")).resolve()
INPUT_DIR=BASE_DIR/"input"; JOBS_DIR=BASE_DIR/"jobs"
INPUT_DIR.mkdir(parents=True,exist_ok=True); JOBS_DIR.mkdir(parents=True,exist_ok=True)
HOST=os.getenv("HOST","0.0.0.0"); PORT=int(os.getenv("PORT","8000"))
WHISPER_DEVICE=os.getenv("WHISPER_DEVICE","cpu"); WHISPER_COMPUTE_TYPE=os.getenv("WHISPER_COMPUTE_TYPE","int8")
MAX_INPUT_SIZE=int(os.getenv("MAX_INPUT_SIZE",str(500*1024*1024)))
HTTP_TIMEOUT=float(os.getenv("HTTP_TIMEOUT","60")); CALLBACK_TIMEOUT=float(os.getenv("CALLBACK_TIMEOUT","30"))
API_TOKEN=os.getenv("API_TOKEN","")
ALLOW_PRIVATE_URLS=os.getenv("ALLOW_PRIVATE_URLS","false").lower()=="true"
ALLOWED_URL_HOSTS={x.strip().lower() for x in os.getenv("ALLOWED_URL_HOSTS","").split(",") if x.strip()}
ALLOW_PRIVATE_CALLBACKS=os.getenv("ALLOW_PRIVATE_CALLBACKS","false").lower()=="true"
ALLOWED_CALLBACK_HOSTS={x.strip().lower() for x in os.getenv("ALLOWED_CALLBACK_HOSTS","").split(",") if x.strip()}
