FROM python:3.10.4-slim-buster

ENV PYTHONUNBUFFERED=1

COPY ./poetry.lock /
COPY ./pyproject.toml /

RUN pip install poetry && \
    poetry config virtualenvs.create false && \
    poetry install --no-dev && \
    rm -rf ~/.cache/pypoetry

COPY ./one-influxdb.py /

CMD [ "python", "/one-influxdb.py" ]
