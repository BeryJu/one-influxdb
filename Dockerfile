FROM python:3.10.1-slim-buster as locker

COPY ./Pipfile /
COPY ./Pipfile.lock /

WORKDIR /app/

RUN pip install pipenv && \
    pipenv lock -r > requirements.txt && \
    pipenv lock -rd > requirements-dev.txt

FROM python:3.10.1-slim-buster

ENV PYTHONUNBUFFERED=1

COPY --from=locker /app/requirements.txt /
COPY --from=locker /app/requirements-dev.txt /

RUN pip install -r requirements.txt  --no-cache-dir

COPY ./one-influxdb.py /

CMD [ "python", "/one-influxdb.py" ]
