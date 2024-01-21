from flask import Flask, render_template
import json
import requests

# My modules
import jsonUtility

# Load JSON services
f = open("services.json")
if f == None:
    ValueError("[LOG]: Error when opening the services.json")
services = json.load(f)

def urlOfService(services, service):
    if service in services:
        return services[service]["url"]
    return "NaN"

def statusService(services, service):
    url = urlOfService(services, service)
    if url == "NaN":
        print("[LOG]: You passed a service that is not tracked")
        return False
    
    needToCheck = jsonUtility.deltaTimeService(services, service)
    
    if needToCheck:
        print(f"[LOG]: HTTP request for {services[service]["url"]}")
        response = requests.get(url).status_code < 400 # Starting 400 codes are error for HTTP GET
        
        jsonUtility.updateStatus(services, service, response)
        
        return response
    else:
        return services[service]["Last status"]


# Start the Flask app
app = Flask("UCLouvainDown")

@app.route("/")
def index():
    return render_template("index.html", serviceList=services.keys())

@app.route("/<service>")
def service(service):
    url = urlOfService(services, service)
    UP = statusService(services, service)
    
    return render_template("itemWebsite.html", service=service, url=url, UP=UP)

if __name__ == "__main__":
    app.run(host="0.0.0.0", debug=True)