from pathlib import Path
from typing_extensions import Annotated
import logging
import os

import uvicorn

from fastapi import FastAPI, Request, Form, responses, status, HTTPException
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.logger import logger as fastapi_logger
from fastapi.templating import Jinja2Templates

from python_freeipa import ClientMeta
from python_freeipa.exceptions import InvalidSessionPassword

from starlette.middleware.sessions import SessionMiddleware
from starlette.responses import RedirectResponse, HTMLResponse

import gspread


APP_NAME = "Studio Assignation"

BASE_PATH = Path(__file__).resolve().parent
TEMPLATES = Jinja2Templates(directory=str(BASE_PATH / "templates"))
app = FastAPI(title="Studio Assignation")
app.mount("/static", StaticFiles(directory="static"), name="static")
app.runtime_data = {}


def _is_debug():
    return os.environ.get("DEBUG_MODE", "false") in ["true", "True"]


logger = logging.getLogger("gunicorn.error")
fastapi_logger.handlers = logger.handlers
fastapi_logger.setLevel(logging.INFO)

logging.getLogger().setLevel(logging.INFO)


def _target_allowed(target):
    if target in ["/login/", "/authenticate_user/"] or target.startswith("/static/"):
        return True

    return False


@app.middleware("http")
async def middleware(request: Request, call_next):
    if not app.runtime_data.get("spreadsheet"):
        # Path to the Google authentication file should be in the environment variables
        google_auth_file_path = os.getenv("GOOGLE_AUTH_FILE_PATH")
        # Get the Google client instance
        gclient = gspread.service_account(filename=google_auth_file_path)
        # The document key should also be in the environment variables
        document_key = os.getenv("GOOGLE_SHEETS_DOC_KEY")
        # Retrieve and save the assignation doc
        app.runtime_data["spreadsheet"] = gclient.open_by_key(document_key)

    if request.session.get("logged", False) or _target_allowed(request.url.path):
        response = await call_next(request)
        return response

    logger.info("User is not authenticated. Redirecting to login page...")

    return responses.RedirectResponse(
        "/login/",
        status_code=status.HTTP_307_TEMPORARY_REDIRECT
    )


@app.get("/")
async def root(_: Request) -> RedirectResponse:
    return responses.RedirectResponse("/timeline/", status_code=status.HTTP_308_PERMANENT_REDIRECT)


class AuthenticationException(HTTPException):
    def __init__(self, status_code, detail=None, headers=None):
        super().__init__(status_code=status_code, detail=detail, headers=headers)


@app.get("/login/")
async def login(request: Request) -> HTMLResponse:
    data = {"app_name": APP_NAME, "request": request}
    error_msg = app.runtime_data.get("auth_error_msg")
    username_value = app.runtime_data.get("username_value")

    if error_msg:
        data["error_msg"] = error_msg
        app.runtime_data["auth_error_msg"] = None
    if username_value:
        data["username_value"] = username_value
        app.runtime_data["username_value"] = None

    return TEMPLATES.TemplateResponse("login.html", data)


@app.exception_handler(AuthenticationException)
async def authentication_exception_handler(_: Request, exc: AuthenticationException):
    app.runtime_data["auth_error_msg"] = exc.detail
    logger.error(exc.detail)

    return responses.RedirectResponse("/login/", status_code=status.HTTP_302_FOUND)


def _user_member_of_any(comparison_groups, user_groups):
    for user_group in user_groups:
        if user_group in comparison_groups:
            return True

    return False


@app.post("/authenticate_user/")
async def authenticate_user(request: Request,
                            username: Annotated[str, Form()],
                            password: Annotated[str, Form()],
                            remember: Annotated[str, Form()] = False):
    app.runtime_data["username_value"] = username

    host = os.environ.get("FREEIPA_HOST")

    if not host:
        error_msg = f"User Authentication: Can't authenticate user because FREEIPA_HOST environment variable is not set."
        raise AuthenticationException(status_code=status.HTTP_401_UNAUTHORIZED, detail=error_msg)

    client = ClientMeta(host, verify_ssl=False)
    try:
        client.login(username, password)
    except ConnectionError:
        error_msg = f"User Authentication: Given url '{host}' doesn't seem to be valid."
        raise AuthenticationException(status_code=status.HTTP_401_UNAUTHORIZED, detail=error_msg)
    except InvalidSessionPassword:
        error_msg = f"User Authentication: Given credentials for user '{username}' doesn't seem to be valid."
        raise AuthenticationException(status_code=status.HTTP_401_UNAUTHORIZED, detail=error_msg)

    user_data = client.user_find(o_uid=username)

    if not user_data["count"]:
        error_msg = f"User Authentication: User '{username}' doesn't exist."
        raise AuthenticationException(status_code=status.HTTP_401_UNAUTHORIZED, detail=error_msg)
    elif user_data["count"] > 1:
        error_msg = f"User Authentication: Multiple user with the same UID has been found for user '{username}'."
        raise AuthenticationException(status_code=status.HTTP_401_UNAUTHORIZED, detail=error_msg)

    user_groups = user_data["result"][0]["memberof_group"]
    if not _user_member_of_any(user_groups, ["prod-coordo", "prod-admin", "prod-dev"]):
        error_msg = f"User Authentication: User '{username}' does not belong to 'prod-coordo', 'prod-admin', 'prod-dev'"
        raise AuthenticationException(status_code=status.HTTP_401_UNAUTHORIZED, detail=error_msg)

    logger.info(f"User Authentication: User '{username}' has been correctly authenticated. Root page will be loading.")
    request.session["logged"] = True

    return responses.RedirectResponse("/timeline/", status_code=status.HTTP_302_FOUND)


@app.get("/projects-assignation-data/")
async def get_projects_assignation_data(_: Request):
    table_header_rows = None
    projects_data = {}

    worksheets_names = app.runtime_data["spreadsheet"].worksheets()
    worksheets_names.sort()

    if "PROJECT_PERMANENTS" in worksheets_names:
        worksheets_names.remove("PROJECT_PERMANENTS")
        worksheets_names.insert(0, "PROJECT_PERMANENTS")

    for sheet in worksheets_names:
        if not sheet.title.startswith("PROJECT_"):
            continue
        project_name = sheet.title.removeprefix("PROJECT_")
        values = sheet.get_all_values()

        if not table_header_rows:
            table_header_rows = values[:3]

        projects_data[project_name] = values[3:]

    return JSONResponse(content={
        "table_header_rows": table_header_rows,
        "projects_data": projects_data
    })


@app.get("/workstation-types-data/")
async def get_workstation_types_data(_: Request):
    ws_types_data = {}
    ws_sheet = app.runtime_data["spreadsheet"].worksheet("INTERNAL_WS_DATA")
    values = ws_sheet.get_all_values()

    for row in values:
        if not row[0]:
            continue

        ws_types_data[row[0]] = {
            "background_color": row[1],
            "color": row[2],
            "pool": [x for x in row[3:] if x]
        }

    return JSONResponse(content=ws_types_data)


@app.get("/timeline/")
async def timeline(request: Request):
    data = {"app_name": APP_NAME, "request": request}
    return TEMPLATES.TemplateResponse("timeline.html", data)


@app.get("/about/")
async def timeline(request: Request):
    data = {"app_name": APP_NAME, "request": request}
    return TEMPLATES.TemplateResponse("about.html", data)


@app.post("/assignation-update/")
async def assignation_update(request: Request):
    body = await request.json()

    project_sheet = app.runtime_data["spreadsheet"].worksheet("PROJECT_"+body["projectID"])
    project_sheet.update_cell(body["rowIndex"]+1, body["colIndex"]+1, body["newValue"])

    return JSONResponse(content={"status": "ok"})


app.add_middleware(SessionMiddleware, secret_key="StudioAssignation")


if __name__ == "__main__":
    config = uvicorn.Config("app:app", host="0.0.0.0", port=5000, reload=True, log_level="debug")
    server = uvicorn.Server(config)
    server.run()
