"""FastAPI app."""
import os

from fastapi import  FastAPI, APIRouter
from starlette.applications import Starlette
from starlette.routing import Mount
from starlette.middleware.cors import CORSMiddleware
from PLATER.services.config import config
from PLATER.services.util.logutil import LoggingUtil
from PLATER.services.app_trapi_1_0 import APP_TRAPI_1_0
from PLATER.services.app_trapi_1_1 import APP_TRAPI_1_1


TITLE = config.get('PLATER_TITLE', 'Plater API')
VERSION = os.environ.get('PLATER_VERSION', '1.0.0')

logger = LoggingUtil.init_logging(
    __name__,
    config.get('logging_level'),
    config.get('logging_format'),
)

APP = FastAPI()

APP.include_router(APP_TRAPI_1_1.router)
APP.include_router(APP_TRAPI_1_0.router)


# CORS
APP.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

if __name__ == '__main__':
    import uvicorn
    uvicorn.run(APP, host='0.0.0.0', port=8080)
