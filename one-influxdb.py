#!/usr/bin/env python
import os
from time import sleep, time
from typing import Any, Dict, List
from xmlrpc.client import ServerProxy
from traceback import print_exc

from lxml import etree

from influxdb import InfluxDBClient
from requests.exceptions import ConnectionError


class Collector:

    influx: InfluxDBClient

    one_client: ServerProxy
    _auth_string: str

    def __init__(self, one_server: str, one_auth: str, influx_kwargs):
        self.one_client = ServerProxy(one_server)
        self._auth_string = one_auth
        self.influx = InfluxDBClient(**influx_kwargs)

    def collect_host(self) -> List[Dict[Any, Any]]:
        """VMM Host Performance Data / onehost performance"""
        points = []
        host_pool = etree.fromstring(
            self.one_client.one.hostpool.info(self._auth_string)[1]
        )
        for host in host_pool.xpath("//HOST"):
            # host performance data

            tags = {
                "cluster": host.find("CLUSTER").text,
                "host": host.find("NAME").text,
                "version": host.find("TEMPLATE/VERSION").text,
                "cpu": host.find("TEMPLATE/MODELNAME").text,
                "hypervisor": host.find("TEMPLATE/HYPERVISOR").text,
            }

            zombies = host.find("TEMPLATE/TOTAL_ZOMBIES")
            if zombies is not None:
                zombies = int(zombies.text)
            else:
                zombies = 0

            points += [
                {
                    "measurement": "host_cpu",
                    "tags": tags,
                    "fields": {
                        "usage": int(host.find("HOST_SHARE/CPU_USAGE").text),
                        "max": int(host.find("HOST_SHARE/MAX_CPU").text),
                        "used": int(host.find("HOST_SHARE/USED_CPU").text),
                        "free": int(host.find("HOST_SHARE/USED_CPU").text),
                    },
                },
                {
                    "measurement": "host_memory",
                    "tags": tags,
                    "fields": {
                        "usage": int(host.find("HOST_SHARE/MEM_USAGE").text),
                        "max": int(host.find("HOST_SHARE/MAX_MEM").text),
                        "used": int(host.find("HOST_SHARE/USED_MEM").text),
                        "free": int(host.find("HOST_SHARE/FREE_MEM").text),
                    },
                },
                {
                    "measurement": "host_network",
                    "tags": tags,
                    "fields": {
                        "net_rx": int(host.find("TEMPLATE/NETRX").text),
                        "net_tx": int(host.find("TEMPLATE/NETTX").text),
                    },
                },
                {
                    "measurement": "rvms",
                    "tags": tags,
                    "fields": {
                        "value": int(host.find("HOST_SHARE/RUNNING_VMS").text),
                        "zombies": zombies,
                    },
                },
            ]
        return points

    def collect_vdc(self) -> List[Dict[Any, Any]]:
        """VDC Performance Data / onegroup performance"""
        group_pool = etree.fromstring(
            self.one_client.one.grouppool.info(self._auth_string)[1]
        )
        points = []
        for group in group_pool.xpath("GROUP"):
            # graph performance data
            group_id = int(group.findtext("ID"))
            if group_id < 1:
                print(f"[collect_vdc] Ignoring group {group_id}")
                continue

            for quota in group_pool.xpath("QUOTAS"):
                quota_id = int(quota.findtext("ID"))
                if quota_id == group_id:
                    group_name = group.xpath("NAME")[0].text
                    # push metrics to influx
                    points += [
                        {
                            "measurement": "quota",
                            "tags": {"group": group_name,},
                            "fields": {
                                "cpu": float(quota.xpath("VM_QUOTA/VM/CPU")[0].text),
                                "cpu_used": float(
                                    quota.xpath("VM_QUOTA/VM/CPU_USED")[0].text
                                ),
                                "memory": float(
                                    quota.xpath("VM_QUOTA/VM/MEMORY")[0].text
                                ),
                                "memory_used": float(
                                    quota.xpath("VM_QUOTA/VM/MEMORY_USED")[0].text
                                ),
                                "vms": float(quota.xpath("VM_QUOTA/VM/VMS")[0].text),
                                "vms_used": float(
                                    quota.xpath("VM_QUOTA/VM/VMS_USED")[0].text
                                ),
                            },
                        },
                    ]
        return points

    def collect_vm(self) -> List[Dict[Any, Any]]:
        """VM Performance Data / onevm performance"""
        points = []
        vm_pool = etree.fromstring(
            self.one_client.one.vmpool.infoextended(
                self._auth_string,  # Session
                -2,  # All Items
                -1,  # Range start (all items)
                -1,  # Range end (all item)
                3,  # Only active VMs
            )[1]
        )
        for vm in vm_pool.xpath("//VM"):
            vm_id = vm.findtext("ID")
            vm_name = vm.findtext("NAME")
            mon = vm.find("MONITORING")

            points += [
                {
                    "measurement": "vm",
                    "tags": {"vm_id": vm_id, "vm_name": vm_name},
                    "fields": {
                        "disk_read_iops": int(mon.findtext("DISKRDIOPS")),
                        "disk_read_bytes": int(mon.findtext("DISKRDBYTES")),
                        "disk_write_iops": int(mon.findtext("DISKWRIOPS")),
                        "disk_write_bytes": int(mon.findtext("DISKWRBYTES")),
                        "cpu": float(mon.findtext("CPU")),
                        "memory": int(mon.findtext("MEMORY")),
                        "net_rx": int(mon.findtext("NETRX")),
                        "net_tx": int(mon.findtext("NETTX")),
                    },
                },
            ]
        return points

    def collect_datastore(self) -> List[Dict[Any, Any]]:
        """Datastore data"""
        datastore_pool = etree.fromstring(
            self.one_client.one.datastorepool.info(self._auth_string)[1]
        )
        points = []
        for ds in datastore_pool:

            ds_id = int(ds.find("ID").text)
            if ds_id < 100:
                # Ignore default datastores
                continue

            points += [
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
        return points

    def collect_all(self):
        all_points = []
        collectors = [
            self.collect_host,
            self.collect_vdc,
            self.collect_vm,
            self.collect_datastore,
        ]
        for col in collectors:
            try:
                all_points += col()
            except Exception as exc:
                print(f"[collection] error: {exc}")
                print_exc()
        try:
            self.influx.write_points(all_points)
            print(f"[influx] wrote {len(all_points)} Metrics")
        except ConnectionError as exc:
            print(f"[influx] error: {exc}")
            print_exc()


if __name__ == "__main__":
    ONE_SERVER = os.getenv("ONE_SERVER", "http://localhost:2633")
    try:
        ONE_AUTH = open("/var/lib/one/.one/one_auth", "r").read().rstrip("\n")
    except FileNotFoundError:
        ONE_AUTH = (
            f"{os.getenv('ONE_USER', 'oneadmin')}:{os.getenv('ONE_PASS', 'oneadmin')}"
        )

    INFLUX_SERVER = os.getenv("INFLUX_SERVER", "localhost")
    INFLUX_PORT = int(os.getenv("INFLUX_PORT", "8086"))
    INFLUX_DB = os.getenv("INFLUX_DB", "one")

    INTERVAL = int(os.getenv("INTERVAL", 300))

    c = Collector(
        ONE_SERVER,
        ONE_AUTH,
        {"host": INFLUX_SERVER, "port": INFLUX_PORT, "database": INFLUX_DB,},
    )
    print(
        f"[main] collecting from server {ONE_SERVER} as user {ONE_AUTH.split(':')[0]}"
    )
    print(f"[main] writing to server {INFLUX_SERVER}:{INFLUX_PORT}/{INFLUX_DB}")
    while True:
        start_time = time()
        print("[collection] start")
        c.collect_all()
        end_time = time()
        print(f"[collection] ended in {end_time - start_time}ms")
        sleep(INTERVAL)
