import os
from getpass import getpass
from xmlrpc.client import ServerProxy

from lxml import etree

from influxdb import InfluxDBClient

ONE_SERVER = os.getenv("ONE_SERVER", "http://localhost:2633")
ONE_USER = os.getenv("ONE_USER")
ONE_PASSWORD = os.getenv("ONE_PASSWORD")

INFLUX_SERVER = os.getenv("INFLUX_SERVER", "localhost")
INFLUX_PORT = int(os.getenv("INFLUX_PORT", "8086"))
INFLUX_DB = os.getenv("INFLUX_DB", "one")

influx = InfluxDBClient(host=INFLUX_SERVER, port=INFLUX_PORT, database=INFLUX_DB)

one_client = ServerProxy(ONE_SERVER)
auth_string = f"{ONE_USER}:{ONE_PASSWORD}"

#
# VMM Host Performance Data / onehost performance
#
host_pool = etree.fromstring(one_client.one.hostpool.info(auth_string)[1])
for host in host_pool.xpath("//HOST"):
    # host performance data
    cluster = host.find("CLUSTER").text
    hostname = host.find("NAME").text
    mem_usage = int(host.find("HOST_SHARE/MEM_USAGE").text)
    max_mem = int(host.find("HOST_SHARE/MAX_MEM").text)
    used_mem = int(host.find("HOST_SHARE/USED_MEM").text)
    free_mem = int(host.find("HOST_SHARE/FREE_MEM").text)
    cpu_usage = int(host.find("HOST_SHARE/CPU_USAGE").text)
    max_cpu = int(host.find("HOST_SHARE/MAX_CPU").text)
    used_cpu = int(host.find("HOST_SHARE/USED_CPU").text)
    free_cpu = int(host.find("HOST_SHARE/FREE_CPU").text)
    rvms = int(host.find("HOST_SHARE/RUNNING_VMS").text)

    # push metrics to influx
    influx.write_points(
        [
            {
                "measurement": "host_cpu",
                "tags": {"cluster": cluster, "host": hostname},
                "fields": {
                    "usage": cpu_usage,
                    "max": max_cpu,
                    "used": used_cpu,
                    "free": free_cpu,
                },
            },
            {
                "measurement": "host_memory",
                "tags": {"cluster": cluster, "host": hostname},
                "fields": {
                    "usage": mem_usage,
                    "max": max_mem,
                    "used": used_mem,
                    "free": free_mem,
                },
            },
            {
                "measurement": "rvms",
                "tags": {"cluster": cluster, "host": hostname},
                "fields": {"value": rvms,},
            },
        ]
    )

#
# VDC Performance Data / onegroup performance
#
group_pool = etree.fromstring(one_client.one.grouppool.info(auth_string)[1])
for group in group_pool.xpath('GROUP'):

    # graph performance data
    group_id = int(group.findtext('ID'))

    for quota in group_pool.xpath('QUOTAS'):
        quota_id = int(quota.findtext('ID'))
        if quota_id == group_id:
            group_name          = group.xpath('NAME')[0].text
            try:
                group_cpu           = quota.xpath('VM_QUOTA/VM/CPU')[0].text
                group_cpu_used      = quota.xpath('VM_QUOTA/VM/CPU_USED')[0].text
                group_memory        = quota.xpath('VM_QUOTA/VM/MEMORY')[0].text
                group_memory_used   = quota.xpath('VM_QUOTA/VM/MEMORY_USED')[0].text
                group_vms           = quota.xpath('VM_QUOTA/VM/VMS')[0].text
                group_vms_used      = quota.xpath('VM_QUOTA/VM/VMS_USED')[0].text

                # push metrics to influx
                influx.write_points(
                    [
                        {
                            "measurement": "quota",
                            "tags": {"group": group_name,},
                            "fields": {
                                "cpu": group_cpu,
                                "cpu_used": group_cpu_used,
                                "memory": group_memory,
                                "memory_used": group_memory_used,
                                "vms": group_vms,
                                "vms_used": group_vms_used
                            },
                        },
                    ]
                )
            except:
                continue


#
# VM Performance Data / onevm performance
#
vm_pool = etree.fromstring(
    one_client.one.vmpool.infoextended(
        auth_string, # Session
        -2, # All Items
        -1, # Range start (all items)
        -1, # Range end (all item)
        3 # Only active VMs
    )[1]
)
for vm in vm_pool.xpath("//VM"):

    vm_id = vm.find("ID").text
    mon = vm.find("MONITORING")
    vm_diskrdiops = int(mon.findtext("DISKRDIOPS"))
    vm_diskrdbytes = int(mon.findtext("DISKRDBYTES"))
    vm_diskwriops = int(mon.findtext("DISKWRIOPS"))
    vm_diskwrbytes = int(mon.findtext("DISKWRBYTES"))
    vm_cpu = float(mon.findtext("CPU"))
    vm_memory = int(mon.findtext("MEMORY"))
    vm_netrx = int(mon.findtext("NETRX"))
    vm_nettx = int(mon.findtext("NETTX"))

    # send out metrics to influx
    influx.write_points(
        [
            {
                "measurement": "vm",
                "tags": {"vm_id": vm_id},
                "fields": {
                    "disk_read_iops": vm_diskrdiops,
                    "disk_read_bytes": vm_diskrdbytes,
                    "disk_write_iops": vm_diskwriops,
                    "disk_write_bytes": vm_diskwrbytes,
                    "cpu": vm_cpu,
                    "memory": vm_memory,
                    "net_rx": vm_netrx,
                    "net_tx": vm_nettx,
                },
            },
        ]
    )


#
# Datastore data
#
datastore_pool = etree.fromstring(
    one_client.one.datastorepool.info(auth_string)[1]
)
for ds in datastore_pool:

    ds_id = int(ds.find('ID').text)
    if ds_id < 100:
        # Ignore default datastores
        continue

    influx.write_points(
        [
            {
                "measurement": "datastore",
                "tags": {"ds_id": ds_id, "ds_name": ds.findtext("NAME")},
                "fields": {
                    "total": int(ds.findtext("TOTAL_MB")),
                    "free": int(ds.findtext("FREE_MB")),
                    "used": int(ds.findtext("USED_MB")),
                },
            },
        ]
    )
