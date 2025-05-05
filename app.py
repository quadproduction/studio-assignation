import os
import re
import time
import logging
from pathlib import Path
from typing_extensions import Annotated

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
app.runtime_data = {
    "data": {
        "retrieval_in_progress": False,
        "last_retrieval_epoch": 0,
        "projects_data": None,
        "ws_types_data": None
    }
}


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
    if request.session.get("logged", False) or _target_allowed(request.url.path):
        if not app.runtime_data.get("spreadsheet_assignation"):
            # Path to the Google authentication file should be in the environment variables
            google_auth_file_path = os.getenv("GOOGLE_AUTH_FILE_PATH")
            # Get the Google client instance
            gclient = gspread.service_account(filename=google_auth_file_path)
            # The document key should also be in the environment variables
            assign_document_key = os.getenv("GOOGLE_SHEETS_ASSIGN_DOC_KEY")
            ws_document_key = os.getenv("GOOGLE_SHEETS_WORKSTATIONS_DOC_KEY")
            # Retrieve and save the assignation doc
            app.runtime_data["spreadsheet_assignation"] = gclient.open_by_key(assign_document_key)
            app.runtime_data["spreadsheet_ws"] = gclient.open_by_key(ws_document_key)

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


async def _get_projects_assignation_data():
    table_header_rows = None
    projects_data = {}

    worksheets = app.runtime_data["spreadsheet_assignation"].worksheets()
    worksheets.sort(key=lambda x: x.title)

    # Find the permanents project and move it at start if exists
    perm_project = next((obj for obj in worksheets if obj.title == "PROJECT_PERMANENTS"), None)
    if perm_project:
        worksheets.remove(perm_project)
        worksheets.insert(0, perm_project)

    for sheet in worksheets:
        if not sheet.title.startswith("PROJECT_"):
            continue
        project_name = sheet.title.removeprefix("PROJECT_")
        values = sheet.get_all_values()

        project_color_code = values[0][0].strip()
        if not project_color_code or not re.fullmatch(r"^#[A-Za-z0-9]{6}$", project_color_code):
            project_color_code = "#FF0000"

        values[0][0] = ""

        if not table_header_rows:
            table_header_rows = values[:3]

        projects_data[project_name] = {
            "color_code": project_color_code,
            "values": values[3:]
        }

    return [table_header_rows, projects_data]


@app.get("/projects-assignation-data/")
async def get_projects_assignation_data(force: bool = False):
    while app.runtime_data["data"]["retrieval_in_progress"]:
        time.sleep(2)

    if force or (int(time.time()) - app.runtime_data["data"]["last_retrieval_epoch"]) >= 300:
        app.runtime_data["data"]["retrieval_in_progress"] = True
        [app.runtime_data["data"]["table_header_rows"],
         app.runtime_data["data"]["projects_data"]] = await _get_projects_assignation_data()
        app.runtime_data["data"]["ws_types_data"] = await _get_workstation_types_data()
        app.runtime_data["data"]["ws_list"] = await _get_workstation_list()
        app.runtime_data["data"]["last_retrieval_epoch"] = int(time.time())
        app.runtime_data["data"]["retrieval_in_progress"] = False

    return JSONResponse(content={
        "table_header_rows": app.runtime_data["data"]["table_header_rows"],
        "projects_data": app.runtime_data["data"]["projects_data"]
    })


async def _get_workstation_types_data():
    ws_types_data = {}
    ws_sheet = app.runtime_data["spreadsheet_assignation"].worksheet("INTERNAL_WS_DATA")
    values = ws_sheet.get_all_values()

    for row in values:
        if not row[0]:
            continue

        ws_types_data[row[0]] = {
            "background_color": row[1],
            "color": row[2],
            "pool": [x for x in row[3:] if x]
        }

    return ws_types_data


@app.get("/workstation-types-data/")
async def get_workstation_types_data(_: Request):
    return JSONResponse(content=app.runtime_data["data"]["ws_types_data"])


@app.get("/timeline/")
async def timeline(request: Request):
    data = {"app_name": APP_NAME, "request": request}
    return TEMPLATES.TemplateResponse("timeline.html", data)


async def _get_workstation_list():
    ws_list = {}
    ws_sheet = app.runtime_data["spreadsheet_ws"].worksheet("MACHINES")
    values = ws_sheet.get('F:F')
    print(values)

    for row in values:
        if not row[0] or not row[0].strip():
            continue

        ws_list[row[0]] = True

    del ws_list["ID"]

    return list(ws_list.keys())


@app.get("/workstation-list/")
async def get_workstation_list(_: Request):
    return JSONResponse(content=app.runtime_data["data"]["ws_list"])


@app.get("/workstations/")
async def people(request: Request):
    data = {"app_name": APP_NAME, "request": request}
    return TEMPLATES.TemplateResponse("workstations.html", data)


@app.get("/people/")
async def people(request: Request):
    data = {"app_name": APP_NAME, "request": request}
    return TEMPLATES.TemplateResponse("people.html", data)


@app.get("/blueprint/")
async def people(request: Request):
    data = {"app_name": APP_NAME, "request": request}
    return TEMPLATES.TemplateResponse("blueprint.html", data)


@app.get("/about/")
async def timeline(request: Request):
    data = {"app_name": APP_NAME, "request": request}
    return TEMPLATES.TemplateResponse("about.html", data)


@app.post("/assignation-update/")
async def assignation_update(request: Request):
    body = await request.json()

    project_sheet = app.runtime_data["spreadsheet_assignation"].worksheet("PROJECT_"+body["projectID"])
    project_sheet.update_cell(body["rowIndex"]+1, body["colIndex"]+1, body["newValue"])

    app.runtime_data["data"]["projects_data"][body["projectID"]]["values"][body["rowIndex"]-3][body["colIndex"]] = body["newValue"]

    return JSONResponse(content={"status": "ok"})


app.add_middleware(SessionMiddleware, secret_key="StudioAssignation")


if __name__ == "__main__":
    config = uvicorn.Config("app:app", host="0.0.0.0", port=5000, reload=True, log_level="debug")
    server = uvicorn.Server(config)
    server.run()
