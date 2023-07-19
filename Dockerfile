FROM joyzoursky/python-chromedriver:3.9

RUN apt-get update 

RUN apt install -y wget \
    git \
    g++ \
    default-jre \
    gcc \
    python-dev \
    pkg-config \
    vim \
    curl \
    build-essential

RUN pip install pip --upgrade

COPY requirements.txt .
RUN pip install -r requirements.txt

COPY main.py outputs/ /

CMD celery -A main.celery_app worker --loglevel=info -c ${WORKER_COUNT} & uvicorn main:app --host 0.0.0.0 --port 8080
