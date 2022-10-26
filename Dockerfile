FROM python:3

WORKDIR /usr/src/app

COPY requirements.txt ./

RUN pip install --no-cache-dir -r requirements.txt

COPY . .

RUN ln -sf /dev/stdout /usr/src/app/logs/app.log

CMD [ "python", "./run.py" ]