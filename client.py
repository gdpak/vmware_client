#!/usr/bin/env python

import env
import gflags
import json
import os
from pyVmomi import vim
import re
import socket

from util.base import log
# For FLAGS host_ssh_key, hypervisor_internal_ip, hypervisor_username.
import util.cluster.consts   # pylint: disable=unused-import
import util.hypervisor.base.esx_flags  # pylint: disable=unused-import
from util.misc.retry import retry_with_exp_backoff
from util.net.ssh_client import SSHClient
from util.hypervisor.esx_host import get_vcenter
from cluster.client import genesis_utils
from cluster.client.genesis.networking import esx_dvs_helper as helper

FLAGS = gflags.FLAGS
pyvmomi_socket_timeout_secs = 300
gflags.DEFINE_string(
        "esx_port_key_external_id_marker",
        "extId:",
        "String identifier for external ID")

def get_user_credentials(host_ip=None):
  """
  User credentials for the object.
  """
  host_ip = host_ip or FLAGS.hypervisor_internal_ip
  ssh_client = SSHClient(host_ip, FLAGS.hypervisor_username,
                         private_key=FLAGS.host_ssh_key)
  ret, stdout, stderr = ssh_client.execute("echo 1")
  if ret != 0:
    log.WARNING("Failed creating ssh client with key, attempting with "
                "default password, stdout %s stderr %s" % (stdout, stderr))
    ssh_client = SSHClient(host_ip, FLAGS.hypervisor_username,
                           password=FLAGS.default_cvm_password)

  # Derive relative path to the OTP script. This is required during upgrade
  # where we need to pick otp script from data installer directory.
  pattern = re.compile("/home/nutanix/data/installer/.*?/")
  filepath = os.path.abspath(__file__)
  match = pattern.search(filepath)

  if match:
    egg_path = filepath[:match.end()]
    otp_path = os.path.join(egg_path, "lib/esx5/get_one_time_password.py")
  else:
    # Pick default path
    otp_path = "/home/nutanix/cluster/lib/esx5/get_one_time_password.py"

  ssh_client.transfer_to(otp_path, "/")
  rsc_group = FLAGS.nutanix_resource_pool_on_esx
  mem_limit = FLAGS.nutanix_resource_pool_size_in_mb
  mem_min = FLAGS.nutanix_resource_pool_min_size_in_mb
  min_limit = FLAGS.nutanix_resource_pool_min_limit_size_in_mb
  if create_ntnx_rsc_pool(rsc_group, mem_limit, mem_min, min_limit, ssh_client):
    log.INFO("Using nutanix resource pool %s to run "
                "get_one_time_password.py" % rsc_group)
    cmd = ("USER=vpxuser python ++group=%s /get_one_time_password.py" %
           rsc_group)
  else:
    log.WARNING("Using ssh resource pool to run get_one_time_password.py")
    cmd = ("USER=vpxuser python /get_one_time_password.py")
  ret, out, err = ssh_client.execute(cmd, escape_cmd=True)
  if ret != 0:
    log.ERROR("Unable to execute OTP command ret %s out %s err %s" %
              (ret, out, err))
    return None
  dict = json.loads(out)
  return dict

def create_ntnx_rsc_pool(rsc_group, mem_limit, mem_min, min_limit, ssh_client):
  """
  Creates resource pool on ESX to execute scripts and commands.

  If we fail to set memory limits, we are marking it as failure. Using a pool
  without limits would cause more harm with runaway scripts or commands.
  Returns:
    False - failed to create resource pool or failed to set memory limit
    True  - otherwise
  """
  # Check if the pool already exists
  cmd = ("localcli --plugin-dir /usr/lib/vmware/esxcli/int sched group list "
         "-g %s -l 1" % rsc_group)
  ret, out, err = ssh_client.execute(cmd)
  if ret != 0:
    # Pool doesn't exist
    log.ERROR("Unable to fetch nutanix resource pool, ret %s out %s err %s" %
              (ret, out, err))
    log.INFO("Creating default nutanix resource pool %s on ESX" % rsc_group)
    (parent_group, _, name) = rsc_group.rpartition("/")
    cmd = ("localcli --plugin-dir /usr/lib/vmware/esxcli/int sched group add "
          "-g %s -n %s" % (parent_group, name))
    ret, out, err = ssh_client.execute(cmd)
    if ret != 0:
      log.ERROR("Failed to create nutanix resource pool, ret %s out %s err %s" %
                (ret, out, err))
      return False

  # Set memory limits on the resource pool
  cmd = ("localcli --plugin-dir=/usr/lib/vmware/esxcli/int sched group "
         "setmemconfig -g %s --max %s --min %s --minlimit %s -u mb" %
         (rsc_group, mem_limit, mem_min, min_limit))
  ret, out, err = ssh_client.execute(cmd)
  if ret != 0:
    log.ERROR("Failed to set memory limits (min, max, minlimt) of (%s, %s, %s) "
              "on ESX resource pool %s, ret %s out %s err %s" %
              (mem_min, mem_limit, mem_min, rsc_group, ret, out, err))
    return False
  return True

class BaseEsxHostObject(object):
  """
  Returns Esx Host object after connecting with pyvim interface.
  """
  def __init__(self, host_ip, user=None, password=None):
    """
    Initializes the object and connects with Present host objects.
    """
    self.user = user
    self.password = password
    self.host_ip = host_ip
    self.host_obj = None

    self.service_instance = None
    self.service_content = None
    self.datacenter = None
    self.compute_resource = None
    self.vim_connection_error = None
    self.connect_with_retries()

  def __del__(self):
    """
    Object reference is being deleted. Terminate the connection.
    """
    self.disconnect()

  def get_login_credentials(self):
    """
    Populate the user credentials if not set.
    """
    if self.user and self.password:
      return (True, self.user, self.password)

    user_dict = get_user_credentials(self.host_ip)
    if not user_dict:
      log.ERROR("Unable to get credentials for Esx communication")
      return (False, None, None)

    return (True, user_dict["username"], user_dict["password"])

  def set_host_params(self):
    """
    Once connection made, fill other params as datacenter, compute resources.
    """
    try:
      if self.is_socket_open():
        self.service_content = self.service_instance.content
        self.datacenter = (
            self.service_instance.content.rootFolder.childEntity[0])
        self.compute_resource = self.datacenter.hostFolder.childEntity[0]
        if self.compute_resource.host:
          self.host_obj = self.compute_resource.host[0]
    except Exception as ee:
      log.ERROR("Initializing host failed %s" % ee)
      return False
    return True

  def connect_with_retries(self):
    """
    Args:
     None

    This function is a wrapper on connect function for retrying attempts to
    connect to the hypervisor.

    Returns:
     False: when connection is unnsuccessful even after the retries.
     True: when connection is successful to the hypervisor.
    """
    for _ in retry_with_exp_backoff(
            slot_time_ms=FLAGS.esx_retry_slot_time_ms,
            max_delay_ms=FLAGS.esx_retry_max_delay_ms,
            max_retries=FLAGS.esx_retry_max_retries):
      log.INFO("Attempting to connect to host with IP %s" % self.host_ip)

      ret = self.connect()

      if ret:
        log.INFO("Connection to host is successful on host ip %s"
                  % self.host_ip)
        return True

      if type(self.vim_connection_error) == socket.error:
        log.ERROR("Unreachable host with IP %s, will not retry connecting, "
                  "breaking connection." % self.host_ip)
        self.vim_connection_error = None
        return False

      log.INFO('Retrying connection to %s' % self.host_ip)
    return False

  def connect(self):
    """
    Connects with host object.
    """
    (ret, username, password) = self.get_login_credentials()
    if not ret:
      log.ERROR("Cannot find credentials for connection")
      return False

    from pyVim.connect import SmartConnectNoSSL
    try:
      self.service_instance = SmartConnectNoSSL(
          user=username, pwd=password, host=self.host_ip,
          socketTimeout=pyvmomi_socket_timeout_secs)
    except socket.error as socket_exception:
      self.vim_connection_error = socket_exception
      log.ERROR("Connection to host %s failed %s" % (self.host_ip,
                                                     socket_exception))
      return False
    except vim.fault.HostConnectFault as e:
      log.ERROR("Connection to host %s failed %s" % (self.host_ip, e.msg))
      return False
    except Exception as e:
      log.ERROR("Connection to host %s failed %s" % (self.host_ip, e))
      return False
    return self.set_host_params()

  def is_socket_open(self):
    """
    Returns true if Smart Connect connection is open with host ip, else returns
    False.
    """
    if self.service_instance:
      return True
    return False

  def is_connected(self):
    """
    Returns True if host object is correctly instantiated, else return False.
    """
    if self.host_obj:
      return True
    return False

  def disconnect(self):
    """
    Disconnects with host interface.
    """
    if self.service_instance:
      from pyVim.connect import Disconnect
      Disconnect(self.service_instance)
      # Explicitly closing all the connections,
      # since Disconnect() may leave connections open.
      try:
        self.service_instance._stub.DropConnections()
      except Exception as ex:
        log.ERROR("Error while closing host connection. Error: %s" % str(ex))
      self.service_instance = None
      self.datacenter = None
      self.compute_resource = None
      self.service_content = None
      self.host_obj = None

  def get_host(self):
    """
    Returns the host object.
    """
    return self.host_obj

  def get_management_server_ip(self):
    """
    Get the management-server IP address from the host.
    Returns the management-server IP address. Can be None if the host is not
    connected to a vCenter Server.
    """
    return self.host_obj.summary.managementServerIp

  def get_esx_host_uuid(self):
    """
    Get the UUID of the host.

    Returns the UUID of ESX host.
    """
    return self.host_obj.hardware.systemInfo.uuid

  def get_port_key_from_external_id(self, portgroup):
    from xml.etree import ElementTree
    if isinstance(portgroup, str):
        external_id_marker = FLAGS.esx_port_key_external_id_marker
        if portgroup.startswith(external_id_marker):
            portgroup = portgroup[len(external_id_marker):]
            print("portgroup: %s" %portgroup)
        else:
            errStr = "port ID is not in external ID Format %s" % portgroup
            return (False, errStr)
    else:
        errStr = "port ID is not in external ID Format %s" % portgroup
        return (False, errStr)

    host_ip = FLAGS.hypervisor_internal_ip
    ssh_client = SSHClient(host_ip, FLAGS.hypervisor_username,
            private_key=FLAGS.host_ssh_key)
    ret, stdout, stderr = ssh_client.execute(
            "localcli --formatter=xml network ip interface list ")
    if ret != 0:
        errstr = "failed in executing network ip interface list"
        print (errstr)
        return (False, errstr)
    network_ip_list = ElementTree.fromstring(stdout)
    root = network_ip_list.find('root')[0]
    nics = root.findall('structure')
    for nic in nics:
        for nic_keys in nic.items():
            for nic_attribs in nic_keys.attrib.iter():
                print (nic_attribs.tag, nic_attribs.attrib, nic_atttribs.text)


def get_portgroup_mor(host_ip, portgroup_name):
  ret, vcenter = helper.get_vcenter_object()
  if not ret:
    return (False, None, None)
  host_obj = vcenter.lookup_host_by_ip(host_ip)
  if not host_obj:
    return (False, None, None)
  for dvs in vcenter.all_virtual_distributed_switches:
    for portgroup in dvs.portgroup:
      if portgroup.name == portgroup_name:
        return (True, portgroup, host_obj)
      #print(portgroup.config.distributedVirtualSwitch.uuid, portgroup.name, portgroup.key)
  return (False, "port group not found", None)

def create_vnic(host_ip, ip_address, netmask, host_physical_network):
  ret, portgroup, host_obj = get_portgroup_mor(host_ip, host_physical_network)
  if not ret:
    return (False, "nic create failed")
  switch_uuid = portgroup.config.distributedVirtualSwitch.uuid
  portgroup_key = portgroup.key

  vmk = vim.host.VirtualNic.Specification()
  vmk.ip = vim.host.IpConfig()
  vmk.ip.ipAddress = ip_address
  vmk.ip.subnetMask = netmask
  dvs_port = vim.dvs.PortConnection()
  dvs_port.switchUuid = switch_uuid
  dvs_port.portgroupKey = portgroup_key
  vmk.distributedVirtualPort = dvs_port
  try:
    vmk_id = host_obj.configManager.networkSystem.AddVirtualNic(
               portgroup="", nic=vmk)
  except Exception as ex:
    print("vmkernel create failed: %s" % str(ex))
    return False
  print(vmk_id)


def get_portkey_of_host_interface(host_ip, host_physical_network):
    ret, portgroup, host_obj = get_portgroup_mor(host_ip, host_physical_network)
    if not ret:
        return (False, None)
    switch_uuid = portgroup.config.distributedVirtualSwitch.uuid
    portgroup_key = portgroup.key

    port_key, external_id = None, None
    all_vmknics = host_obj.configManager.networkSystem.networkInfo.vnic
    for vmk in all_vmknics:
        if not vmk.spec.distributedVirtualPort:
            continue
        if (vmk.spec.distributedVirtualPort.switchUuid == switch_uuid and
            vmk.spec.distributedVirtualPort.portgroupKey == portgroup_key):
            port_key = vmk.spec.distributedVirtualPort.portKey
            external_id = vmk.spec.externalId
            break
    if not port_key:
        print ("Can not find port key")
        return (False, "Failed to fidn port key")
    if not external_id:
        return (True, port_key)
    return (True, FLAGS.esx_port_key_external_id_marker+external_id)


'''
base_host = BaseEsxHostObject("10.46.1.214", user="administrator@vsphere.local",
                              password="Nutanix/4u")
create_vnic("10.47.242.69", "172.16.9.1", "255.255.0.0", 'DPG-HOST-BP')
base_host = BaseEsxHostObject("192.168.5.1")

base_host.connect_with_retries()
host_obj=base_host.get_host()
all_vmknics = host_obj.configManager.networkSystem.networkInfo.vnic
print (all_vmknics)
get_portgroup_mor("10.47.242.69", 'DPG-HOST-BP')
'''
ret, pk = get_portkey_of_host_interface("10.47.242.69", 'DPG-HOST-BP')
if ret:
  print("PK: %s" % pk)
base_host = BaseEsxHostObject("192.168.5.1")
base_host.get_port_key_from_external_id(portgroup=pk)
