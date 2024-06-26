try:
    import uvicorn as uvicorn

    from fastapi import APIRouter, FastAPI, Path, Request
    from fastapi.exceptions import RequestValidationError
    from fastapi.responses import JSONResponse
    from fastapi.middleware.wsgi import WSGIMiddleware

    from flask import Flask, render_template, request, make_response, send_from_directory, session
    from flask_babel import Babel, _
    # _ to evaluate the text and translate it
    from flask_babel import lazy_gettext as _l
    import sqlite3
    # lazy_gettext is like the _ but handle the later evaluation of the text
        
    # To handle apscheduler
    from apscheduler.schedulers.asyncio import AsyncIOScheduler
    from apscheduler.jobstores.memory import MemoryJobStore
    from contextlib import asynccontextmanager
    from lock import *
    #from apscheduler.schedulers.background import BackgroundScheduler  # To schedule the check
    #import datetime
    
    # Own modules
    from models import *
    from fastapi_custom import ALL_HTTP_METHODS, hide_422, hide_default_responses, use_route_names_as_operation_ids
    from utilities import *
    from logger_config import *
    
    import sql
except ImportError as e:
    logger.warning(f"[LOG] Error on startup: not all packages could be properly imported:\n{e}.")
    raise exit(1)
except ImportWarning as w:
    logger.warning(f"[LOG] Warning on startup: not all packages could be properly imported:\n{w}")


# Load JSON files ######################################################################################################
JSON_FILE_WEBHOOKS = "webhooks.json"
webhooks = Webhooks.load_from_json_file(JSON_FILE_WEBHOOKS)

JSON_FILE_SERVICES = "services.json"
services = Services.load_from_json_file(JSON_FILE_SERVICES, webhooks=webhooks)
webhooks._set_services(webhooks)

# APSCHEDULER ##########################################################################################################

def refreshServices():
    logger.info("[LOG]: Refreshing the services")
    session = requests.Session()
    
    services.update_status(session)

    session.close()
    
    services_name = services.names
    
    # Connect to the SQLite database
    conn = sqlite3.connect('data/outage.sqlite3')
    cursor = conn.cursor()
    
    for service in services_name:
        up, time = services.get_service(service).up_time()
        # convert time into unix timestamp
        time = int(time.timestamp())
        
        table_name = service.replace('.', '_')  # Replace dots with underscores for table name
        table_name = table_name.replace('-', '_')  # Replace dashes with underscores for table name

        logger.info(f"[LOG]: Updating {service} status to {up} at {time}")
        
        # Insert the status update
        cursor.execute(f'''
            INSERT INTO {table_name} (timestamp, status, user)
            VALUES (?, ?, ?)
        ''', (time, up, 0))
        
        logger.info(f"[LOG]: {service} status updated successfully")
    
    # Commit the transaction
    conn.commit()

    # Close the connection
    conn.close()

    logger.info("[LOG]: Finished Refreshing the services")



# ------------------ Start the Flask app ------------------
app = Flask("UCLouvainDown")

LANGUAGES=["en", "fr"]
wanted_language = None

def get_locale():
    if wanted_language is None:
        return request.accept_languages.best_match(LANGUAGES)
    return wanted_language

babel = Babel(app, locale_selector=get_locale)


@app.route("/")
async def index():
    """Render homepage, with an overview of all services."""
    logger.info(f"[LOG]: HTTP request for homepage")
    return render_template("index.html", serviceList=all_service_details().root.values(), get_locale=get_locale())

@app.route("/language")
def languageChange():
    global wanted_language
    user_language = request.args.get("choice")
    
    logger.info(f"[LOG]: User requested {user_language} language")
    
    if user_language in LANGUAGES:
        wanted_language = user_language
        return "200"
    
    logger.warning(f"[LOG]: User passed a non supported language")
    return "400"

@app.route("/serviceList")
def serviceList():
    dictService = []
    
    for service in services:
        dictService.append(dict(service=service, reportedStatus=service.is_up_user))
    return render_template("serviceList.html", servicesInfo=dictService)


@app.route("/request")
def requestServie():
    serviceName = request.args.get('service-name', "")
    url = request.args.get('url', "")
    info = request.args.get('info', "")
    
    # No form submitted No feedback
    feedback = "" 
            
    if len(serviceName) > 0:
        # If someone wrote in the form
        # IF I REMOVE THIS LINE NO MORE NEED FOR THAT DEPRECATED FILE
        feedback = "Form submitted successfully!"

    return render_template("request.html", feedback=feedback)


# To handle error reporting
@app.route('/process', methods=['GET'])
def process():
    user_choice = request.args.get('choice', 'default_value')
    service = request.args.get('service', None)

    if user_choice == 'yes':
        logger.info('Great! The website is working for you.')
        
        ### HERE GOES THE SQL FOR USER REPORT

        return _('Great! The website is working for you.')
    elif user_choice == 'no':
        logger.info('The website is down for me too.')
        
        ### HERE GOES THE SQL FOR USER REPORT


        return _('The website is down for me too.')
    else:
        return _('Invalid choice or no choice provided')


@app.route("/extract")
def extractLog():
    get_what_to_extract = request.args.get("get")

    if get_what_to_extract.lower() == "all" or get_what_to_extract.lower == "outage":
        # send the data/outage.sqlite3 file
        return send_from_directory("data", "outage.sqlite3")
    # Make some smarter query using SQL
    else:
        return render_template("404.html")


@app.route('/robots.txt')
@app.route('/sitemap.xml')
def static_from_root():
    return send_from_directory(app.static_folder, request.path[1:])   

@app.route("/info")
def info_website():
    return render_template("info.html")


@app.route("/<service>")
async def service_details_app(service: str):
    """Render a page with details of one service."""
    if service not in services:
        return page_not_found()
    print(f"[LOG]: HTTP request for {service}")
    """
    data: looks like this: {"time": ["2021-10-10 10:10:10"], "status": [1]}
    """
    timeArray, UPArray = sql.get_latest_status(service)
    userTimeArray, userUPArray = sql.get_latest_user_report(service)
    percent_up = sql.get_percentage_uptime(service)
    percent_down = 1 - percent_up
        
    return render_template("itemWebsite.html", service=service_details(service), data={"time": timeArray, "status": UPArray}, data_user={"time": userTimeArray, "status": userUPArray} , percent={"up": percent_up, "down": percent_down})


@app.errorhandler(404)
def page_not_found(_=None):
    return render_template("404.html")

# Setup Scheduler to periodically check the status of the website
# When the scheduler need to be stopped

fd = acquire("myfile.lock")

if fd is None:
    logger.error("[LOG]: Could not acquire lock, exiting.")
    @asynccontextmanager
    async def lifespan(api: FastAPI):
        #Dummy
        yield
else:
    jobStores = {
        "default": MemoryJobStore()
    }

    scheduler = AsyncIOScheduler(jobstores=jobStores, timezone="UTC")

    # Execute the refreshServices function every RECHECK_AFTER minutes
    @scheduler.scheduled_job("interval", seconds=RECHECK_AFTER, next_run_time=dt.datetime.utcnow())
    def scheduledRefresh():
        refreshServices() 
    
    @asynccontextmanager
    async def lifespan(api: FastAPI):
        # Start the scheduler
        scheduler.start()
        yield
        # Stop the scheduler
        scheduler.shutdown()



# Define the FastAPI app and its routes ################################################################################
# !!! DO NOT move the `api.mount(...)` statement before the `@api....` functions !!! ###################################
api = FastAPI(
    title="UCLouvainDown API",
    version="v0.1.0",
    summary="**A simple interface with the *UCLouvainDown* backend!**",
    # Do not descend the following line after the """ down, it will break openapi
    description="""This API provides an interface with the *UCLouvainDown* backend, allowing to check if services of the 
    University of Louvain (UCLouvain), as well as some other services often used by its students, are up and running 
    (or not).
    </br>
    <ul>
      <li>Non-developpers might be better of using the [UCLouvainDown website](/) rather than passing by this API. It
        is a graphic representation of the same data.</li>
      <li>A webhook interface is also provided, please refer to [the webhook section](/api/docs#tag/Webhooks) of the 
        documentation for the details.</li>
    </ul>
    An issue or request? Please let us know [over on github](https://github.com/Tfloow/UCLouvainDown).
    """,
    docs_url=None,
    redoc_url="/api/docs",
    # terms_of_service="", TODO
    contact={
        "name": "Wouter Vermeulen",
        "email": "wouter.vermeulen@student.uclouvain.be",
    },
    lifespan=lifespan,
    # license_info={}, TODO
)

# Define common responses
api_unkown_url_response = JSONResponse(
    content={"detail": "Sorry, could not find that URL. Please check the documentation at '/api/docs'!"},
    status_code=404
)
api_unkown_service_response = JSONResponse(
    content={"detail": "The requested service is not tracked by this application. Pleasy verify the listed "
                       "services at '/api/services/overview'!"},
    status_code=404
)
webhook_400_response = JSONResponse(
    content={"detail": "One or more of the services listed as those to track, aren't tracked by the application. "
                       "Pleasy verify the listed services at '/api/services/overview'."},
    status_code=400)
webhook_403_response = JSONResponse(
    content={"detail": "The given password does not correspond to the one given when creating the webhook. If entered "
                       "manually, please verify you made no typo."},
    status_code=403)
webhook_404_response = JSONResponse(
    content={"detail": "The webhook for which modifications were asked can't be found."},
    status_code=404)


# FastAPI routes
@api.get(
    "/api/services/overview",
    response_model=List[str],
    responses={
        "200": {"content": {"application/json": {"schema": {"examples": [["Inginious", "ADE-scheduler"]]}}}}
    }
)
def services_overview():
    """
    Get a list of the names of all services that are tracked (that is, regularly checked on their status) by this
    application. This are the only names accepted in requests requiring a service name, such as for example
    [`service_details`](/api/docs#operation/service_details).
    """
    return services.names


@api.get(
    "/api/services/up/all",
    response_model=Dict[str, bool],
    responses={"200": {"content": {"application/json": {"schema": {"examples": [
        {"Inginious": True, "ADE-scheduler": False}]}}}}
    }
)
def all_service_statuses():
    """
    Get the current status (up or down) for all tracked services. The keys in the response are the name of a
    tracked service, values are booleans: `true` means the service is up and running, `false` means it is down.

    **Note**: *for most applications, the status for only a few services are needed. Please use the
      [`Service Status` endpoint](/api/docs#operation/service_status) instead in those cases!*

    **Note**: *a status change might be reported with a small delay. Applications requiring 100% up-to-date
    information that wish the verify when the status was last checked, should use the
     [`All Service Details` endpoint](/api/docs#operation/all_service_details) or the
     [`Service Details` endpoint](/api/docs#operation/service_details).*
    """
    return {service.name: service.is_up for service in services.root}


@api.get(
    "/api/services/up/{service:str}",
    response_model=bool,
    responses={
        "200": {"content": {"application/json": {"schema": {"title": None}}}},
        "404": {"detail": "Service not tracked", "model": HTTPError}
    }
)
def service_status(
        service: Annotated[
            str,
            Path(
                description="The service for which to get the status. It must be in the list of tracked services"
                            "that can be requested at [this endpoint](/api/docs#operation/services_overview).")
        ]
):
    """
    Get the status of a specific service: `true` if the service is up and running, `false` if it is down.

    **Note**: *a status change might be reported with a small delay. Applications requiring 100% up-to-date
    information that wish the verify when the status was last checked, should use the
     [`Service Details` endpoint](/api/docs#operation/service_details).*

    \f

    Service shall be a valid service, werkzeug already checks this for us with the enumeration of supported services.
    """
    if service not in services:
        return api_unkown_service_response

    return services.get_service(service).is_up


@api.get(
    "/api/services/all",
    response_model=Services
)
def all_service_details():
    """
    Get the following information on all tracked services:
      * The service url.
      * The current status of the service: is the service up or down (a status change might be reported with a small
        delay - for applications requiring 100% up-to-date information be sure to verify that the `last_checked` field
        in the response is recent enough).
      * The last time the status of the service was checked.

    **Note**: *for most applications, the details for only a few services are needed. Please use the
      [`service details endpoint`](/api/docs#operation/service_details) instead in those cases!*
    """
    return services


@api.get(
    "/api/services/{service:str}",
    response_model=Service,
    responses={
        "404": {"detail": "Service not tracked", "model": HTTPError}
    }
)
def service_details(
        service: Annotated[
            str,
            Path(
                description="The service for which to get the information. It must be in the list of tracked services"
                            "that can be requested at [this endpoint](/api/docs#operation/services_overview).")
        ]
):
    """
    Get the following information on a service by passing its name:
      * The service url.
      * The current status of the service: is the service up or down (a status change might be reported with a small
        delay - for applications requiring 100% up-to-date information be sure to verify that the `last_checked` field
        in the response is recent enough).
      * The last time the status of the service was checked.

    **Note:** *a list of all tracked services can be found at [this endpoint](/api/docs#operation/services_overview).*

    \f

    Service shall be a valid service, werkzeug already checks this for us with the enumeration of supported services.
    """
    if service not in services:
        return api_unkown_service_response

    return services.get_service(service)


# Purely for the openapi documentation for the webhook callbacks: create an APIRouter
webhook_callback_router = APIRouter()


@webhook_callback_router.post("{$callback_url}", responses=None)
def status_change_notification(_: ServiceStatusChange):
    """
    Receive the information that one of the services had a status change: it went from UP to DOWN or from DOWN to
    UP recently.

    \f

    NO IMPLEMENTATION NECESSARY: this is strictly to get the callback documentation in the openapi specs.
    """
    pass


@api.post(
    "/api/webhooks",
    response_model=WebhookResponse,
    status_code=201,
    tags=["Webhooks"],
    responses={
        "400": {"model": HTTPError, "description": "Unkown service tracking requested"}
    },
    callbacks=webhook_callback_router.routes
)
def create_webhook(
        webhook: Webhook,
        password: Annotated[
            str, Body(
                description="A password associated with the webhook, needed to update or delete it later.")
        ]
):
    """
    Create a webhook to receive updates if the status of one of the requested tracked sites changes.
    """
    for service in webhook.tracked_services:
        if service not in services:
            return webhook_400_response
    if not webhook.tracked_services:  # Empty list
        webhook.tracked_services = services.names

    return webhooks.add_webhook(webhook, password=password)


@api.patch(
    "/api/webhooks/{hook_id:int}",
    response_model=WebhookResponse,
    tags=["Webhooks"],
    responses={
        "400": {"model": HTTPError, "description": "Unkown service tracking requested"},
        "403": {"model": HTTPError, "description": "Wrong password"},
        "404": {"model": HTTPError, "description": "Webhook id unkown"}},
    callbacks=webhook_callback_router.routes
)
def update_webhook(
        hook_id: Annotated[int, Path(description="The id of the webhook to update.")],
        webhook_patches: WebhookPatches,
        password: Annotated[str, Body(
            description="A password associated with the webhook, must be the same as the one given when creating "
                        "the webhook.")
        ]
):
    """
    Update a created webhook to track other services, or to change the callback url.
    """
    if not webhooks.hook_id_exists(hook_id):
        return webhook_404_response
    if not verify_password(webhooks.get_password_hash(hook_id), password):
        return webhook_403_response

    if webhook_patches.tracked_services is not None:
        for service in webhook_patches.tracked_services:
            if service not in services:
                return webhook_400_response
        if not webhook_patches.tracked_services:  # Empty list
            webhook_patches.tracked_services = services.names

    return webhooks.update_webhook(hook_id, webhook_patches)


@hide_422
@api.delete(
    "/api/webhooks/{hook_id:int}",
    status_code=204,
    tags=["Webhooks"],
    responses={
        "403": {"model": HTTPError, "description": "Wrong password"},
        "404": {"model": HTTPError, "description": "Webhook id unkown"}
    }
)
def delete_webhook(
        hook_id: Annotated[int, Path(description="The id of the webhook to delete.")],
        password: Annotated[str, Body(
            description="A password associated with the webhook, must be the same as the one given when creating "
                        "the webhook.")
        ]
):
    """
    Delete a created webhook. No more callbacks will be made based on its content.
    """
    if not webhooks.hook_id_exists(hook_id):
        return webhook_404_response
    if not verify_password(webhooks.get_password_hash(hook_id), password):
        return webhook_403_response

    webhooks.delete_webhook(hook_id)


# Add FastAPI error handling ###########################################################################################
@api.api_route("/api", status_code=404, include_in_schema=False, methods=ALL_HTTP_METHODS)
def api_request_not_found() -> JSONResponse:
    """
    Any paths with `/api` and `/api/...` that have not been catched before are invalid.
    If this endpoint wasn't added, the client would recieve the 404 HTML site from the UCLouvainDown website.
    """
    return api_unkown_url_response


@api.api_route("/api/{_:path}", status_code=404, include_in_schema=False, methods=ALL_HTTP_METHODS)
def api_request_not_found_bis(_: str = "") -> JSONResponse:
    """See `api_request_not_found`."""
    return api_request_not_found()


@api.exception_handler(RequestValidationError)
def custom_exception_handler(request_: Request, exc: RequestValidationError):
    """
    If the `hook_id` given in /api/webhooks/{hook_id} requests is incorrect, that should be a 404 response,
    not a 422 pydantic ValidationError response.
    """
    if request_.url.path[:14] == "/api/webhooks/":
        for err in exc.errors():
            if err['loc'][0] == 'path' and err['loc'][1] == 'hook_id':
                return webhook_404_response

    # If no error was catched, just return the 422 error as normal
    return JSONResponse(
        status_code=422,
        content={'detail': exc.errors(), 'body': exc.body},
    )


# Couple the Flask app and the FastAPI app #############################################################################
api.mount("/", WSGIMiddleware(app))


# Modify the openapi docs a little #####################################################################################
use_route_names_as_operation_ids(api)
hide_default_responses(api)


# Run the whole application ############################################################################################
if __name__ == "__main__":
    uvicorn.run(api)
    # app.run(host="0.0.0.0", debug=True)