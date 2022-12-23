# one-influxdb

OpenNebula 6.6 includes a native Prometheus exporter, hence this project is archived: https://docs.opennebula.io/6.6/intro_release_notes/release_notes/whats_new.html#prometheus

Collect OpenNebula Stats and write into InfluxDB

## development

```
export $(cat .env | xargs) && python one-influxdb.py
```
