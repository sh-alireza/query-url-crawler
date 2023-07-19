import csv
import os
from datetime import datetime
import signal
from urllib.parse import urlparse, unquote

from selenium import webdriver
from selenium.webdriver.chrome.options import Options as ChromeOptions
from selenium.webdriver.common.by import By
from tqdm import tqdm
from fastapi import FastAPI, File, UploadFile
from celery.result import AsyncResult
from celery import Celery

USERNAME = str(os.getenv("RABBITMQ_DEFAULT_USER","admin"))
PASSWORD = str(os.getenv("RABBITMQ_DEFAULT_PASS","admin"))
HOST = str(os.getenv("BROKER_SERVICE_HOST","localhost"))
CRAWLER_HOST = str(os.getenv("CRAWLER_SERVICE_HOST","query_url_crawler"))
SITE_LIMIT = int(os.getenv("SITE_LIMIT",20))

PORT = "5672"
OUTPUT_PATH = "outputs"

# Create a new FastAPI instance
app = FastAPI()

# Create a new Celery application instance
celery_app = Celery(
    'tasks',
    broker=f'amqp://{USERNAME}:{PASSWORD}@{HOST}:{PORT}//', # Set the broker URL to connect to a message broker
    backend='rpc://', # Set the backend URL to specify how Celery should store task results
)

@celery_app.task
def get_links_func(rows,output_file_path):
    
    output_file = open(output_file_path, "w",encoding="utf-8")
    writer = csv.writer(output_file,delimiter=",")
    writer.writerow(["urls","query", "link", "website"])
    
    options = ChromeOptions()
    options.add_argument("--headless=new")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--no-sandbox")
    driver = webdriver.Chrome(options=options)
    queries = []
    
    for row in tqdm(rows):
        url = row[0]
        queries = row[1].strip().lower().split(",")
        
        for q in queries:
            taken_links = []
            q = q.strip().lower()
            if q=="":
                writer.writerow([url, q, "", ""])
                continue
            
            new_q = q.replace(" ", "+")
            
            while len(taken_links) < SITE_LIMIT:
                
                driver.get(f"https://www.google.com/search?q={new_q}&start={len(taken_links)}")
                links = driver.find_elements(By.CLASS_NAME,"yuRUbf")
                
                if len(links) == 0:
                    
                    driver.execute_script("window.open('');")
                    driver.switch_to.window(driver.window_handles[0])
                    driver.close()
                    driver.switch_to.window(driver.window_handles[0])
                    continue
                
                for link in links:
                    
                    link_url = link.find_element(By.TAG_NAME,"a").get_attribute("href")
                    decoded_url = unquote(link_url)
                    parsed_url = urlparse(decoded_url)
                    base_url = parsed_url.scheme + "://" + parsed_url.netloc
                    writer.writerow([url, q, decoded_url, base_url])
                    taken_links.append(decoded_url)

    driver.quit()
    output_file.close()

# Define a new endpoint for uploading a CSV file and processing its contents
@app.post("/get-links")
async def get_links(file: UploadFile = File(...)):
    
    # Check that the uploaded file is a CSV file
    if file.content_type != "text/csv":
        return {"error": "your file is not a CSV file"}
    
    # Create a new directory to store the output file
    os.makedirs(OUTPUT_PATH, exist_ok=True)
    
    # Generate a timestamped filename for the output file
    time_now = str(datetime.now()).replace(" ","_")
    output_file_path=os.path.join(OUTPUT_PATH,time_now+"_output.csv")
    output_path = os.path.abspath(output_file_path)
    
    # Read the contents of the uploaded file and parse it as CSV
    contents = await file.read()
    decoded_content = contents.decode('utf-8').splitlines()
    rows = csv.reader(decoded_content)
    
    # Call a Celery task to process the uploaded file in the background
    task = get_links_func.delay(list(rows),output_path)
    
    return {"task_id":task.id,
            "output_path":output_path}

# Define a new endpoint for checking the status of a Celery task
@app.get('/status/{task_id}')
async def get_task_status(task_id: str):
    
    # Get the Celery task object with the specified task ID
    task = AsyncResult(task_id, app=celery_app)
    
    # Get a list of all pending tasks
    i = celery_app.control.inspect()
    pending_tasks = i.active()
    task_ids = [p['id'] for p in pending_tasks[f"celery@{CRAWLER_HOST}"]]
    
    # Check the status of the task and return a response
    if task.state in ["SUCCESS","FAILURE"]:
        return {'status': task.state}
    elif task.state == "PENDING":
        if task_id in task_ids:
            return {'status': "PENDING"}

    return {'status': "Not Exist"}

# Define a new endpoint for getting a list of pending Celery tasks
@app.get('/pending_tasks')
async def get_pending_tasks():
    
    # Use the Celery Inspect class to get a list of all active tasks
    i = celery_app.control.inspect()
    pending_tasks = i.active()
    
    # Extract relevant information from each pending task and add it to the result list
    result = []
    for task in pending_tasks[f"celery@{CRAWLER_HOST}"]:
        task_id = task['id']
        task_start_time = task['time_start']
        task_pid = task['worker_pid']
        result.append({
            "task_id":task_id,
            "task_start_time":task_start_time,
            "task_pid":task_pid
        })
        
    return {"result":result}

# Define a new endpoint for terminating a running Celery task
@app.delete('/process/{task_id}')
async def delete_process(task_id: str):
    
    try:
        
        # Use the Celery Inspect class to get a list of all active tasks
        i = celery_app.control.inspect()
        pending_tasks = i.active()
        
        # Find the worker PID for the specified task ID
        try:
            task_pid = [p['worker_pid'] for p in pending_tasks[f"celery@{CRAWLER_HOST}"] if p['id'] == task_id][0]
        except Exception as e:
            return {"error":str(e)}
        
        # Send a SIGTERM signal to the worker process to terminate the task
        os.kill(task_pid, signal.SIGTERM)
        
        return {'message': f'task with ID {task_id} has been terminated'}
    
    except ProcessLookupError:
        
        return {'error': f'task with ID {task_id} not found'}
