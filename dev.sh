#!/bin/bash -xe
export $(cat .env | xargs) && python one-influxdb.py
