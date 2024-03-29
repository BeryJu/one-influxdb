#!/usr/bin/env python
import os
from enum import IntEnum
from time import sleep, time
from typing import Any, Dict, List, Optional
from xmlrpc.client import ServerProxy
from traceback import print_exc

from lxml import etree
from lxml.etree import _Element

from influxdb import InfluxDBClient
from requests.exceptions import ConnectionError
from sentry_sdk import init, start_transaction
from sentry_sdk.api import capture_exception, start_span
from sentry_sdk.tracing import Transaction

init(
    dsn=os.getenv("SENTRY_DSN"), traces_sample_rate=1.0, environment="production",
)

HOST_STATES = IntEnum(
    "HOST_STATES",
    """INIT MONITORING_MONITORED MONITORED
        ERROR DISABLED MONITORING_ERROR MONITORING_INIT MONITORING_DISABLED
        OFFLINE""",
    start=0,
)


def xml_get_fb(xml_obj, path) -> float:
    """Get xpath value as float with fallback"""
    obj = xml_obj.xpath(path)
    if len(obj) < 1:
        return float(0)
    obj_text = obj[0].text
    return float(obj_text)


def xml_find_fb(xml_obj, path, default=""):
    """XML.find with a fallback"""
    obj = xml_obj.find(path)
    if obj is not None:
        return obj.text
    return default


class CollectorError(Exception):
    pass


class Collector:

    influx: InfluxDBClient

    one_client: ServerProxy
    _auth_string: str

    def __init__(self, one_server: str, one_auth: str, influx_kwargs):
        self.one_client = ServerProxy(one_server)
        self._auth_string = one_auth
        self.influx = InfluxDBClient(**influx_kwargs)

    def one_req(self, method, *args) -> Optional[_Element]:
        calling_method = getattr(self.one_client, method)
        try:
            success, response, error = calling_method(self._auth_string, *args)
            if success:
                return etree.fromstring(response)
            raise CollectorError(f"Request was not successful: {response}")
        except ConnectionError as exc:
            raise CollectorError from exc

    def collect_host(self) -> List[Dict[Any, Any]]:
        """VMM Host Performance Data / onehost performance"""
        points = []
        host_pool = etree.fromstring(
            self.one_client.one.hostpool.info(self._auth_string)[1]
        )
        host_mon = etree.fromstring(
            self.one_client.one.hostpool.monitoring(self._auth_string, 0)[1]
        )
        for host in host_pool.xpath("//HOST"):
            # Host is only monitored if its active
            state = int(host.find("STATE").text)
            if state != HOST_STATES.MONITORED:
                print(
                    f"[collect_host] Host {host.find('ID').text} is not in state monitored, skipping"
                )
                continue

            # host performance data
            tags = {
                "cluster": xml_find_fb(host, "CLUSTER"),
                "host": xml_find_fb(host, "NAME"),
                "version": xml_find_fb(host, "TEMPLATE/VERSION"),
                "cpu": xml_find_fb(host, "TEMPLATE/MODELNAME"),
                "hypervisor": xml_find_fb(host, "TEMPLATE/HYPERVISOR"),
            }

            # Returns the first CAPACITY object where the Host ID matches the current host
            host_id = host.find("ID").text
            monitoring_all = host_mon.xpath(
                f"(//MONITORING/ID[text()={host_id}])[1]/../CAPACITY"
            )
            if not monitoring_all:
                print(
                    f"[collect_host] No Monitoring found for host {host_id}, ignoring"
                )
                continue
            monitoring = monitoring_all[0]
            points += [
                {
                    "measurement": "host_cpu",
                    "tags": tags,
                    "fields": {
                        "allocated": int(xml_find_fb(host, "HOST_SHARE/CPU_USAGE", 0)),
                        "total": int(xml_find_fb(host, "HOST_SHARE/MAX_CPU", 0)),
                        "used": int(xml_find_fb(monitoring, "USED_CPU", 0)),
                        "free": int(xml_find_fb(monitoring, "FREE_CPU", 0)),
                    },
                },
                {
                    "measurement": "host_memory",
                    "tags": tags,
                    "fields": {
                        "allocated": int(xml_find_fb(host, "HOST_SHARE/MEM_USAGE", 0)),
                        "total": int(xml_find_fb(host, "HOST_SHARE/MAX_MEM", 0)),
                        "used": int(xml_find_fb(monitoring, "USED_MEMORY", 0)),
                        "free": int(xml_find_fb(monitoring, "FREE_MEMORY", 0)),
                    },
                },
                {
                    "measurement": "rvms",
                    "tags": tags,
                    "fields": {
                        "value": int(xml_find_fb(host, "HOST_SHARE/RUNNING_VMS", 0)),
                        "zombies": int(xml_find_fb(host, "TEMPLATE/TOTAL_ZOMBIES", 0)),
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
            # oneadmin doesn't have metrics
            if group_id < 1:
                print(f"[collect_vdc] Ignoring group {group_id}")
                continue

            for quota in group_pool.xpath("QUOTAS"):
                quota_id = int(quota.findtext("ID"))
                if quota_id == group_id:
                    group_name = group.xpath("NAME")[0].text
                    # Check if this VDC has quota data
                    if not quota.xpath("VM_QUOTA/VM"):
                        print(
                            f"[collect_vdc] Ignoring group {group_id} (no quota data found)"
                        )
                        continue
                    # push metrics to influx
                    points += [
                        {
                            "measurement": "quota",
                            "tags": {"group": group_name,},
                            "fields": {
                                "cpu": xml_get_fb(quota, "VM_QUOTA/VM/CPU"),
                                "cpu_used": xml_get_fb(quota, "VM_QUOTA/VM/CPU_USED"),
                                "memory": xml_get_fb(quota, "VM_QUOTA/VM/MEMORY"),
                                "memory_used": xml_get_fb(
                                    quota, "VM_QUOTA/VM/MEMORY_USED"
                                ),
                                "running_cpu": xml_get_fb(
                                    quota, "VM_QUOTA/VM/RUNNING_CPU"
                                ),
                                "running_cpu_used": xml_get_fb(
                                    quota, "VM_QUOTA/VM/RUNNING_CPU_USED"
                                ),
                                "running_memory": xml_get_fb(
                                    quota, "VM_QUOTA/VM/RUNNING_MEMORY"
                                ),
                                "running_memory_used": xml_get_fb(
                                    quota, "VM_QUOTA/VM/RUNNING_MEMORY_USED"
                                ),
                                "vms": xml_get_fb(quota, "VM_QUOTA/VM/VMS"),
                                "vms_used": xml_get_fb(quota, "VM_QUOTA/VM/VMS_USED"),
                                "running_vms": xml_get_fb(
                                    quota, "VM_QUOTA/VM/RUNNING_VMS"
                                ),
                                "running_vms_used": xml_get_fb(
                                    quota, "VM_QUOTA/VM/RUNNING_VMS_USED"
                                ),
                                "system_disk_size": xml_get_fb(
                                    quota, "VM_QUOTA/VM/SYSTEM_DISK_SIZE"
                                ),
                                "system_disk_size_used": xml_get_fb(
                                    quota, "VM_QUOTA/VM/SYSTEM_DISK_SIZE_USED"
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

        monitoring = etree.fromstring(
            self.one_client.one.vmpool.monitoring(
                self._auth_string, -2, 0  # Session  # All Items  # Only last records
            )[1]
        )

        for vm in vm_pool.xpath("//VM"):
            vm_id = vm.findtext("ID")
            vm_name = vm.findtext("NAME")
            mon_all = monitoring.xpath(f"(//MONITORING/ID[text()={vm_id}])[1]/..")

            if not mon_all:
                print(f"[collect_vm] No Monitoring found for VM {vm_id}")
                continue
            mon = mon_all[0]
            points += [
                {
                    "measurement": "vm",
                    "tags": {"vm_id": vm_id, "vm_name": vm_name},
                    "fields": {
                        "disk_read_iops": int(mon.findtext("DISKRDIOPS") or 0),
                        "disk_read_bytes": int(mon.findtext("DISKRDBYTES") or 0),
                        "disk_write_iops": int(mon.findtext("DISKWRIOPS") or 0),
                        "disk_write_bytes": int(mon.findtext("DISKWRBYTES") or 0),
                        "cpu": float(mon.findtext("CPU") or 0),
                        "memory": int(mon.findtext("MEMORY") or 0),
                        "net_rx": int(mon.findtext("NETRX") or 0),
                        "net_tx": int(mon.findtext("NETTX") or 0),
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
        with start_transaction(
            name="collection", description="Full Collection run"
        ) as trans:
            trans: Transaction
            for col in collectors:
                try:
                    with trans.start_child(op=f"collect_{col.__name__}"):
                        all_points += col()
                except (ConnectionRefusedError, TimeoutError):
                    print(f"[collection] connection refused")
                except Exception as exc:
                    print(f"[collection] error: {exc}")
                    capture_exception(exc)
                    print_exc()
            try:
                with trans.start_child(op=f"write"):
                    self.influx.write_points(all_points)
                    print(f"[influx] wrote {len(all_points)} Metrics")
            except ConnectionError as exc:
                print(f"[influx] error: {exc}")
                capture_exception(exc)
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
        try:
            c.collect_all()
        except CollectorError:
            print("[collection] collection failed, continuing")
        sleep(INTERVAL)
