import paho.mqtt.client as mqtt
import logging
import math
from enum import Enum

import time
import os
from configparser import ConfigParser
from logging.handlers import TimedRotatingFileHandler

import gzip
import datetime

from collections import deque

from hassapi import Hass

#####################################
# Configuration Section
# Load settings from config.ini file
#####################################
import os
# load config from os
from load_dotenv import load_dotenv
load_dotenv()

log_path = os.getenv('LOG_DIR', 'logs/')
log_filename = os.getenv('LOG_FILENAME', 'chargecontroller.log')

mqtt_host = os.getenv('MQTT_HOST', 'localhost')
mqtt_port = os.getenv('MQTT_PORT', '1883')

homeassistant_host = os.getenv('HASS_HOST', 'http://homeassistant:8123')
homeassistant_token = os.getenv('HASS_TOKEN', '')

######################################
#   Logging
######################################

try:
    os.makedirs(log_path)
    print("Log directory " + log_path + " created")

except FileExistsError:
    pass


class GZipRotator:
    def __call__(self, source, dest):
        os.rename(source, dest)
        f_in = open(dest, "rb")
        f_out = gzip.open("%s.gz" % dest, "wb")
        f_out.writelines(f_in)
        f_out.close()
        f_in.close()
        os.remove(dest)


# get the root logger
rootlogger = logging.getLogger()
# set overall level to debug, default is warning for root logger
rootlogger.setLevel(logging.DEBUG)

# setup logging to file, rotating at midnight
filelog = logging.handlers.TimedRotatingFileHandler(
    log_path + log_filename,
    when="midnight",
    interval=1,
)
filelog.setLevel(logging.INFO)
fileformatter = logging.Formatter("%(asctime)s %(levelname)-8s %(message)s")
filelog.setFormatter(fileformatter)
filelog.rotator = GZipRotator()
rootlogger.addHandler(filelog)

consoleHandler = logging.StreamHandler()
consoleHandler.setFormatter(fileformatter)
consoleHandler.setLevel(logging.INFO)
rootlogger.addHandler(consoleHandler)

# get a logger for my script
logger = logging.getLogger(__name__)

######################################
#   MQTT Config
######################################

client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)

logger.info(f"Connecting to {homeassistant_host} with token {homeassistant_token[:3]}...{homeassistant_token[-3:] if len(homeassistant_token) > 6 else ''}")
# HomeAssistant Client
hass = Hass(hassurl=homeassistant_host,
            token=homeassistant_token)

ha_state = hass.get_state("input_select.wallbox_charge_mode")

class WallBoxMode(Enum):
    off ='Off'
    max_charge = 'Max Charge'
    pv_charge_batt = 'PV Charge (Prefer Battery)'
    pv_charge_charge = 'PV Charge (Prefer Charge)'
    protect_batt = 'Protect Battery'
    min_charge = 'Min Charge'

def get_time():
    now = (datetime.datetime.now()).strftime("%Y-%m-%d %H:%M:%S")
    return now


def on_connect(client, userdata, flags, rc, properties):
    logger.debug("Connected with result code " + str(rc))

    # Subscribing in on_connect() means that if we lose the connection and
    # reconnect then subscriptions will be renewed.
    client.subscribe("vzlogger/#")
    client.subscribe("emon/#")
    client.subscribe("homie/#")


def set_current(new_current):
    logger.debug("Adjusting Current to " + str(new_current) + " A")
    client.publish(
        "homie/Heidelberg-Wallbox/wallbox/max_current/set", new_current, 0, True
    )

WP_Out_Power = 0.0
WP_Out_Power_Queue = deque(maxlen=10)

def on_new_wp_out(client, userdata, message):
    global WP_Out_Power
    WP_Out_Power = float(message.payload.decode("utf-8"))
    WP_Out_Power_Queue.append(WP_Out_Power)
    logger.debug("MQTT: New WP_Out received: " + str(WP_Out_Power))

PV_In_Power = 0.0
PV_In_Queue = deque(maxlen=10)

def on_new_pv_in(client, userdata, message):
    global PV_In_Power
    PV_In_Power = float(message.payload.decode("utf-8"))
    PV_In_Queue.append(PV_In_Power)
    logger.debug("MQTT: New PV Power received: " + str(PV_In_Power))

soc_percent = 0.0

def on_new_soc_percent(client, userdata, message):
    global soc_percent
    soc_percent = float(message.payload.decode("utf-8"))
    logger.debug("MQTT: New SoC/Battery Percent received: " + str(soc_percent))

soc_power = 0.0

def on_new_soc_power(client, userdata, message):
    global soc_power
    soc_power = float(message.payload.decode("utf-8"))
    logger.debug("MQTT: New SoC/Battery Power received: " + str(soc_power))

wb_state = 0

def on_wallbox_state_change(client, userdata, message):
    global wb_state
    temp = message.payload.decode("utf-8")
    try:
        if wb_state == int(temp):
            logger.debug(f"MQTT: Wallbox state still: {str(temp)}")
        else:
            logger.info(f"MQTT: New wallbox state: {str(temp)}")
            log_wallbox_state(int(temp))

        wb_state = int(temp)
    except ValueError:
        logger.error(f"Could not convert wallbox state: {temp}")
        return

wb_consumption = 0.0

def on_wallbox_consumption_change(client, userdata, message):
    global wb_consumption
    temp = message.payload.decode("utf-8")
    try:
        wb_consumption = float(temp)
        logger.info(f"MQTT: New wallbox consumption: {str(temp)}")
    except ValueError:
        logger.error(f"Could not convert wallbox consumption: {temp}")
        return

def log_wallbox_state(wb_state):
    state_messages = {
        0: "Wallbox state: Unknown",
        1: "Wallbox state: Not connected",
        2: "Wallbox state: Connected but not ready",
        3: "Wallbox state: Ready for charging",
        4: "Wallbox state: Vehicle connected, no charging requested",
        5: "Wallbox state: Vehicle connected, charging requested but not permitted",
        6: "Wallbox state: Vehicle connected, charging requested and permitted",
        7: "Wallbox state: Vehicle charging",
    }
    message = state_messages.get(wb_state, f"Unknown wallbox state: {wb_state}")
    if wb_state in state_messages:
        logger.info(message)
    else:
        logger.error(message)

client.message_callback_add("vzlogger/data/chn2/raw", on_new_wp_out)
client.message_callback_add("emon/NodeHuawei/input_power", on_new_pv_in)
client.message_callback_add("emon/NodeHuawei/storage_state_of_capacity", on_new_soc_percent)
client.message_callback_add("emon/NodeHuawei/storage_charge_discharge_power", on_new_soc_power)
client.message_callback_add("homie/Heidelberg-Wallbox/$state", on_wallbox_state_change)
client.message_callback_add("homie/Heidelberg-Wallbox/wallbox/akt_verbrauch", on_wallbox_consumption_change)


client.on_connect = on_connect

try:
    client.connect(mqtt_host,
                  int(mqtt_port),
                  30)
    logger.info(f"Successfully connected to MQTT broker {mqtt_host}:{mqtt_port}")
except ConnectionRefusedError:
    logger.error(f"Connection to MQTT broker {mqtt_host}:{mqtt_port} refused")
except TimeoutError:
    logger.error(f"Connection timeout to MQTT broker {mqtt_host}:{mqtt_port}")
except Exception as e:
    logger.error(f"Unexpected error in MQTT connection: {str(e)}")
    raise

client.loop_start()

def wait_for_wallbox_to_change_consumption(old_consumption):
    start_time = time.time()
    timeout = 60
    logger.info("Waiting for consumption adjustment...")
    while wb_consumption == old_consumption and time.time() - start_time < timeout:
        time.sleep(2)  # Short wait between checks

    logger.info(f"Consumption adjusted after {int(time.time() - start_time)} seconds (from {old_consumption} to {wb_consumption})")

def wait_for_wallbox_to_start_charging():
    # Wait max 60 seconds for charging to start (wb_state = 7)
    start_time = time.time()
    timeout = 60
    logger.info("Waiting for charging to start (wb_state = 7)...")
    while wb_state != 7 and time.time() - start_time < timeout:
        time.sleep(2)  # Short wait between checks

    if wb_state == 7:
        logger.info(f"Vehicle started charging after {int(time.time() - start_time)} seconds")
    else:
        logger.warning("Timeout reached: Vehicle did not start charging")

def deque_calc_avg(queue):
    if not queue or not isinstance(queue, deque):
        logger.warning("Invalid queue for average calculation")
        return 0.0

    try:
        sum_calc = sum(queue)
        return sum_calc / len(queue)
    except (TypeError, ZeroDivisionError) as e:
        logger.error(f"Error in average calculation: {str(e)}")
        return 0.0

MIN_CURRENT = 6  # Minimum charging current in amperes
MAX_CURRENT = 16  # Maximum charging current in amperes

def roundDown(n):
    result = int("{:.0f}".format(n))
    if result < MIN_CURRENT:
        return 0  # Below minimum value - better to stop
    elif result > MAX_CURRENT:
        return MAX_CURRENT  # Return maximum value
    return result


######################################
#   Main Loop
######################################

# todo add power flow of battery ... if battery discharges we need to lower the current
#

enough_pv = False
load_battery = True
charging_car = False
setting_ampere = 0

def loop():
    global charging_car, setting_ampere
    global enough_pv, soc_percent, load_battery, wb_state, ha_state, soc_power

    # Start with 0 current
    set_current(0)

    while True:

        logger.debug("Entering regulation loop")

        # Refresh Home Assistant state
        ha_state = hass.get_state("input_select.wallbox_charge_mode")
        current_state = WallBoxMode.off
        if ha_state.state == WallBoxMode.off.value:
            current_state = WallBoxMode.off
        elif ha_state.state == WallBoxMode.max_charge.value:
            current_state = WallBoxMode.max_charge
        elif ha_state.state == WallBoxMode.pv_charge_charge.value:
            current_state = WallBoxMode.pv_charge_charge
        elif ha_state.state == WallBoxMode.pv_charge_batt.value:
            current_state = WallBoxMode.pv_charge_batt
        elif ha_state.state == WallBoxMode.protect_batt.value:
            current_state = WallBoxMode.protect_batt
        elif ha_state.state == WallBoxMode.min_charge.value:
            current_state = WallBoxMode.min_charge

        if wb_state < 4:
            logger.info("No Vehicle Connected ... skipping")
            set_current(0)
            charging_car = False
            time.sleep(30)
            continue
        elif wb_state == 4:
            charging_car = False
        elif wb_state > 8:
            set_current(0)
            charging_car = False
            time.sleep(30)
            continue

        #Max Charge Mode .. ignore everything else
        if current_state == WallBoxMode.max_charge:
            logger.info("WallBoxMode: Max Charge with 16A")
            set_current(16)
            charging_car = True
            time.sleep(30)
            continue
        elif current_state == WallBoxMode.min_charge:
            logger.info("WallBoxMode: Min Charge with 6A")
            set_current(6)
            charging_car = True
            time.sleep(30)
            continue
        elif current_state == WallBoxMode.off:
            logger.info("WallBoxMode: off")
            set_current(0)
            charging_car = False
            time.sleep(30)
            continue
        elif current_state == WallBoxMode.protect_batt:
            logger.info("WallBoxMode: Protect Battery ... only start if battery is off")
            if soc_percent <= 1.0:
                logger.info(f"Soc Percent is {str(soc_percent)} and SocPower is {str(soc_power)} ... starting")
                set_current(16)
                charging_car = True
            else:
                logger.info(f"Soc Percent is {str(soc_percent)} and SocPower is {str(soc_power)} ... not starting")
                set_current(0)
                charging_car = False
            time.sleep(30)
            continue

        old_current = setting_ampere

        # Condition 1 : PV should produce more then minimum load
        pv_in = deque_calc_avg(PV_In_Queue)
        logger.debug(f"Queue length of PV_In_Queue is {str(len(PV_In_Queue))}, Avg is {str(pv_in)} W")

        # Need at least 3 values in deque for PV production
        if len(PV_In_Queue) <= 3:
            logger.info(f"Warming up ... PV_In_Queue is {str(len(PV_In_Queue))}, Avg is {str(pv_in)} W")
            # Execute every 10 seconds
            time.sleep(20)
            continue
        elif (len(PV_In_Queue) > 3 and pv_in > 1400.0):  # Getting a new PV Input Value every 10 seconds
            enough_pv = True
            logger.info("PV production sufficient to enable charging")
        else:
            enough_pv = False
            set_current(0)
            logger.info("Insufficient PV production for charging - stopping")
            logger.info(f"PV input is {str(pv_in)} W and queue length is {str(len(PV_In_Queue))}")

        # Condition 2: Check Battery
        if enough_pv is True and current_state == WallBoxMode.pv_charge_batt:
            if soc_percent > 98.5:
                load_battery = False
                logger.info(f"Battery at {str(soc_percent)}% - ready to charge vehicle")
            elif charging_car is True and soc_percent < 95.0:
                logger.info(f"Battery at {str(soc_percent)}% - charging active but battery dropped below 95% - stopping")
                set_current(0)
                load_battery = True
                charging_car = False
            else:
                if soc_power > 0:
                    logger.info(f"Battery at {str(soc_percent)}% - prioritizing battery over vehicle charging - battery injection is {str(soc_power)}")
                else:
                    logger.info(f"Battery at {str(soc_percent)}% - prioritizing battery over vehicle charging - battery discharging is {str(soc_power)}")
                load_battery = True
        elif current_state == WallBoxMode.pv_charge_charge:
            load_battery = False
            logger.info(f"Battery at {str(soc_percent)}% - ignoring as mode prioritizes vehicle charging")

        # Condition 3a: Check network injection resp. SoC injection to start charging, if not yet charging
        wp_out = deque_calc_avg(WP_Out_Power_Queue)
        if enough_pv is True and load_battery is False and charging_car is False:
            if current_state == WallBoxMode.pv_charge_batt and wp_out < -1380.0:
                charging_car = True
                setting_ampere = roundDown((-1) * wp_out / 230)
                logger.info(f"Starting PV charging (Prefer Battery) at {str(setting_ampere)} A (WP_Out: {str((-1) * wp_out)} W)")
                set_current(setting_ampere)
                wait_for_wallbox_to_start_charging()
                continue
            else:
                charging_car = False
                logger.info(f"Insufficient power for charging (Prefer Battery) - WP_Out is {str(wp_out)} W")

            if current_state == WallBoxMode.pv_charge_charge and soc_power > 1380:
                # WallBoxMode is to prefer charging rather than loading the battery
                charging_car = True
                setting_ampere = roundDown(soc_power / 230)
                logger.info(f"Starting PV charging (Prefer Charge) at {str(setting_ampere)} A (SoC Power: {str(soc_power)} W)")
                set_current(setting_ampere)
                wait_for_wallbox_to_start_charging() # Car needs to start charging ... takes some time
                continue
            elif current_state == WallBoxMode.pv_charge_charge and soc_percent == 100.0:
                logging.info("SoC at 100% - switching to Prefer Battery mode")
                hass.select_option('input_select.wallbox_charge_mode', WallBoxMode.pv_charge_batt.value)
            else:
                charging_car = False
                logger.info(f"Insufficient power for charging (Prefer Charge) - SoC Power is {str(soc_power)} W")


        # Condition 3b: Car is charging; re-evaluate increase of decrease current
        if (charging_car is True
                and (current_state == WallBoxMode.pv_charge_charge or current_state == WallBoxMode.pv_charge_batt)):
            # Battery discharging
            if soc_power < 0:
                delta = max(1, abs(math.floor(soc_power / 230)))
                setting_ampere -= delta
                logger.info(
                    f"Decreasing charge power by {str(delta)} A from {str(old_current)} A to {str(setting_ampere)} A (battery discharging at {str(soc_power)} W)")

            # Still injecting in the network ... increase by 1 Ampere if injection is more than 400W
            elif wp_out < -400.0:
                setting_ampere += 1
                logger.info(
                    f"Increasing charge power by 1A to {str(setting_ampere)}A (WP_Out: {str(wp_out)} W)")

            elif current_state == WallBoxMode.pv_charge_charge and soc_power > 400:
                if old_current == MAX_CURRENT:
                    logger.info("Already at maximum current - not increasing")
                else:
                    # SoC Injection is more than 1A * 230V = 230W ... increase by 1 Ampere
                    delta = abs(math.floor(soc_power / 230))
                    setting_ampere += delta
                    logger.info(
                        f"Increasing charge power by {str(delta)} A to {str(setting_ampere)} A (SoC Power: {str(soc_power)} W)")

            # Too much load if retrieving from network
            elif wp_out > 50:
                setting_ampere -= max(1, math.ceil(roundDown(wp_out / 230)))
                wp_out_str = str(max(1, math.ceil(roundDown(wp_out / 230))))
                logger.info(
                    f"Decreasing charge power by {wp_out_str}A to {str(setting_ampere)}A (WP_Out: {str(wp_out)} W)")

            # Fix Charging Current
            if setting_ampere < 6:
                logger.info("Below minimum charging level - stopping charging")
                setting_ampere = 0
                charging_car = False

            if setting_ampere > 16:
                logger.info("Reached maximum charging power - limiting to 16A")
                setting_ampere = 16

            if old_current != setting_ampere:
                logger.info(f"Setting new current to {str(setting_ampere)} A (was {str(old_current)} A)")
                set_current(setting_ampere)
                wait_for_wallbox_to_change_consumption(wb_consumption)

            else:
                logger.info(f"Maintaining current at {str(setting_ampere)} A")

        # Execute every 10 seconds
        time.sleep(20)


try:
    logger.info("------------ Wallbox Regulator started ------------")
    time.sleep(5)  # Wait 5 Seconds for MQTT to Connect and Pull Messages
    loop()
except:
    logger.info("------------ Client exited ------------")
    set_current(0) # Setting Current back to 0
    client.disconnect()
    client.loop_stop()
finally:
    logger.info("------------ Stopping client ------------")
    set_current(0) # Setting Current back to 0
    client.disconnect()
    client.loop_stop()
