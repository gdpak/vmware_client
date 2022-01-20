import xml.etree.ElementTree as ET
ext_id='e05d2b07-346f-48cc-b3b6-a58e48f92bcd'

def parse_interface_list(external_id):
    tree = ET.parse('network_ip_interface_list.xml')
    root = tree.find('root')[0] 
    nics = root.findall('structure')
    nic_index = 0
    nic_name_ext_id_maps = []
    for nic in nics:
        nameId = {}
        for nic_field in nic.findall('field'):
            #print(nic_field.attrib)
            if nic_field.attrib['name'] == 'Name':
                for name_val in nic_field.findall('string'):
                    nameId["name"] = name_val.text
            if nic_field.attrib['name'] == 'External ID':
                for ext_id in nic_field.findall('string'):
                    nameId["ext_id"] = ext_id.text
        if nameId:
            nic_name_ext_id_maps.append(nameId)
    for index, nic_name_ext_id_map in enumerate(nic_name_ext_id_maps):
        if nic_name_ext_id_map['ext_id'] == external_id:
            return (True, nic_name_ext_id_maps[index]['name'])

    return (False, "Can not find device_id for external ID: %s" % external_id)

def get_ipv4_address_for_device(device_name):
    tree = ET.parse('ipv4_addr.xml')
    root = tree.find('root')[0] 
    ipv4_addresses = root.findall('structure')
    ipv4_address_maps = []
    for ipv4_address in ipv4_addresses:
        ipv4_address_map = {}
        for ipv4_field in ipv4_address.findall('field'):
            #print(ipv4_field.attrib)
            if ipv4_field.attrib['name'] == 'IPv4 Address':
                for unicast_addr in ipv4_field.findall('string'):
                    ipv4_address_map['address'] = unicast_addr.text
            if ipv4_field.attrib['name'] == 'IPv4 Netmask':
                for mask in ipv4_field.findall('string'):
                    ipv4_address_map['mask'] = mask.text
            if ipv4_field.attrib['name'] == 'Name':
                for name in ipv4_field.findall('string'):
                    ipv4_address_map['name'] = name.text
        if ipv4_address_map:
            ipv4_address_maps.append(ipv4_address_map)

    for i, addr_map in enumerate(ipv4_address_maps):
        if addr_map['name'] == device_name:
            return (True, {"address": ipv4_address_maps[i]["address"],
                "mask": ipv4_address_maps[i]["mask"]})

    return (False, "Can not find ipv4 address on device:%s" % device_name)

ret, device_name = parse_interface_list(ext_id)
if not ret:
   print(device_name)
   exit()
print(ret, device_name)
ret, ipv4_addr = get_ipv4_address_for_device(device_name)
print(ret, ipv4_addr)
