import os
import uvicorn

if __name__ == "__main__":
    host = os.environ.get("DUSHEVYE_API_HOST", "127.0.0.1")
    port = int(os.environ.get("DUSHEVYE_API_PORT", "8088"))
    uvicorn.run("app.main:app", host=host, port=port, proxy_headers=True)
