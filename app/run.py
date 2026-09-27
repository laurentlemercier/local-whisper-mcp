import uvicorn
from .main import app
from .mcp import mount_mcp
from .config import HOST,PORT
mount_mcp(app)
if __name__=="__main__":uvicorn.run(app,host=HOST,port=PORT)
