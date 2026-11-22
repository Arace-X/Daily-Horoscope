"""Start the voice agent server:   python run.py

Listens on http://127.0.0.1:8000 by default. Set PORT (and HOST=0.0.0.0 on a cloud server)
to change that.
"""

import os

import uvicorn
from dotenv import load_dotenv

if __name__ == "__main__":
    load_dotenv(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env"))
    uvicorn.run(
        "app.main:create_app",
        factory=True,
        host=os.getenv("HOST") or "127.0.0.1",
        port=int(os.getenv("PORT") or "8000"),
    )
