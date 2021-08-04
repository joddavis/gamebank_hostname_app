""" Copyright (c) 2021 Cisco and/or its affiliates.
This software is licensed to you under the terms of the Cisco Sample
Code License, Version 1.1 (the "License"). You may obtain a copy of the
License at
           https://developer.cisco.com/docs/licenses
All use of the material herein must be in accordance with the terms of
the License. All rights not expressly granted by the License are
reserved. Unless required by applicable law or agreed to separately in
writing, software distributed under the License is distributed on an "AS
IS" BASIS, WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express
or implied. 
"""

# Import Section
from flask import Flask, render_template, request, url_for, redirect
import requests
from env_var import config
import meraki
from pprint import pprint
from pyats.topology import loader
from webexteamssdk import WebexTeamsAPI
import json

# Global variables
app = Flask(__name__)

meraki_api_key = config['x_cisco_meraki_api_key']
webex_teams_token = config['webex_teams_acces_token']
webex_room_id = config['webex_room_id']
device_username = config['device_username']
device_password = config['device_password']
device_name_filter = config['device_name_filter']
port_number_to_uplink_switch = config['port_number_to_uplink_switch']

# Initalize SDK
dashboard = meraki.DashboardAPI(api_key = meraki_api_key)
webex_api = WebexTeamsAPI(access_token=webex_teams_token)

# Template variables
organizations = []
networks = []
devices = []
selected_organization = []
selected_network = []
configured_hostnames = []

# Methods

def get_hostname_from_uplink_switch(ip_address, device_name, port_id):
    
    # Please note: the testbed file is specifically made for the PoV and 
    # should be adjust to your own environment. 
    # For more information about testbed files: 
    # https://pubhub.devnetcloud.com/media/pyats/docs/topology/example.html
    testbed_file = f"""
devices:
  {device_name}:
    connections:
      cli:
        ip: {ip_address}
        protocol: ssh
        ssh_options: "-c aes256-cbc"
    os: iosxe
    type: iosxe
    credentials:
      default:
        username: {device_username}
        password: {device_password}
"""
    testbed = loader.load(testbed_file)

    device = testbed.devices[device_name]

    device.connect(init_exec_commands=[], init_config_commands=[], log_stdout=False)

    app.logger.info(f"We are going to print the description of the following interface: {port_id}")

    output = device.parse(f'show interfaces {port_id} description')

    app.logger.info(output)

    hostname = output['interfaces'][port_id]['description']

    app.logger.info(f"We are going to disconnect from device {device_name}")
    device.disconnect()

    return hostname

def configure_device(serial_numbers):
    global port_number

    conf_hostnames = []
    for serial_number in serial_numbers:
        try:
            lldp_cdp = dashboard.devices.getDeviceLldpCdp(serial_number)

            app.logger.info(f"CDP/LLDP info of device with serial number: {serial_number}")

            app.logger.info(lldp_cdp)

            # Please note that we are extracting info for a very specific use case
            # Note that uplink switch in this demo is always connected through port 9.
            # We have specified the port_number_to_uplink_switch in the env_var.py

            # In case you want to use logic to determine what port_number to use, 
            # then we can add the logic here. Or we can create a function called 
            # get_uplink_from_lldp_cdp(lldp_cdp) to gather the info about the uplink switch

            ip_addr = lldp_cdp['ports'][port_number_to_uplink_switch]['cdp']['address']
            device = lldp_cdp['ports'][port_number_to_uplink_switch]['cdp']['deviceId'].split('.')[0]
            port_id = lldp_cdp['ports'][port_number_to_uplink_switch]['cdp']['portId']

            meraki_hostname = get_hostname_from_uplink_switch(ip_addr, device, port_id)

            get_device_info = dashboard.devices.getDevice(serial=serial_number)

            # Below, we can update the name of the device. 
            dashboard.devices.updateDevice(serial=serial_number, name=meraki_hostname)

            device_info = {}
            device_info['old_name'] = get_device_info['name']
            device_info['serial'] = get_device_info['serial']
            device_info['updated_name'] = meraki_hostname

            conf_hostnames.append(device_info)
        except:
            app.log_exception(f"Note: we could not configure the device with serial number {serial_number}")
            # Note: we could not update the name
            get_device_info = dashboard.devices.getDevice(serial=serial_number)
            device_info = {}
            device_info['old_name'] = get_device_info['name']
            device_info['serial'] = get_device_info['serial']
            device_info['updated_name'] = get_device_info['name']

    return conf_hostnames

def send_webex_notification(conf_hostnames):
    with open('notification_card.json', 'r') as f:
        adaptive_card = json.loads(f.read())
        f.close()

    for configured_hostname in conf_hostnames:
        textblock_to_add = {
            "type": "TextBlock",
            "text": f"* {configured_hostname['old_name']} -> {configured_hostname['updated_name']} with serial: {configured_hostname['serial']}",
            "wrap": True
            }

        adaptive_card['body'].append(textblock_to_add)

    # Send card on webex
    webex_api.messages.create(webex_room_id, text="If you see this your client cannot render cards.", attachments=[{
        "contentType": "application/vnd.microsoft.card.adaptive",
        "content": adaptive_card
        }])

def get_devices(network_id):
    global device_name_filter

    devices = dashboard.networks.getNetworkDevices(network_id)

    devices = [device for device in devices if device_name_filter in device['model']]

    return devices

# Routes

# Main Page
@app.route('/')
def main():
    global organizations
    organizations = dashboard.organizations.getOrganizations()
    return render_template('columnpage.html', organizations = organizations, networks = [], devices = [], selected_organization = [], selected_network = [], configured_hostnames = [])

# User submits their organization and/or network selection in first rail
@app.route('/select_organization_network', methods=['POST', 'GET'])
def select_organization_network():
    global organizations
    global selected_organization
    global selected_network
    global networks
    global devices

    if request.method == 'POST':
        form_data = request.form
        print(form_data)

        organization_id = form_data['organization_id']

        for organization in organizations:
            if organization_id == organization['id']:
                selected_organization = organization 

        networks = dashboard.organizations.getOrganizationNetworks(organization_id)
        
        if 'network_id' in form_data:
            network_id = form_data['network_id']

            for network in networks:
                if network_id == network['id']:
                    selected_network = network 

            devices = get_devices(network_id)

    return render_template('columnpage.html', organizations = organizations, networks = networks, devices = devices, selected_organization = selected_organization,
         selected_network = selected_network, configured_hostnames = configured_hostnames)

# User selects devices to configure in third rail
@app.route('/select_device', methods=['POST', 'GET'])
def select_device():
    global organizations
    global selected_organization
    global selected_network
    global networks
    global devices
    global configured_hostnames

    if request.method == 'POST':
        form_data = request.form
        app.logger.info(form_data)

        if 'device' in form_data:
            form_dict = dict(form_data.lists())
            serial_numbers = form_dict['device']

            # Configure the hostnames
            configured_hostnames = configure_device(serial_numbers)
            
        if 'webex_summary' in form_data:
            send_webex_notification(configured_hostnames)

        devices = get_devices(selected_network['id'])

    return render_template('columnpage.html', organizations = organizations, networks = networks, devices = devices, selected_organization = selected_organization,
         selected_network = selected_network, configured_hostnames = configured_hostnames)


if __name__ == "__main__":
    app.run(host='0.0.0.0', debug=True)