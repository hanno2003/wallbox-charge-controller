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

# Read config.ini file
config_object = ConfigParser()
config_object.read("config.ini")

# Load configuration sections
general_Config = config_object["general"]
mqtt_Config = config_object["mqtt"]
homeassistant_Config = config_object["homeassistant"]

######################################
#   MQTT Config
######################################

client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)

# HomeAssistant Client
hass = Hass(hassurl=homeassistant_Config["host"],
            token=homeassistant_Config["token"])

ha_state = hass.get_state("input_select.wallbox_charge_mode")

class WallBoxMode(Enum):
    off ='Aus'
    max_charge = 'Max Charge'
    pv_charge_batt = 'PV Charge (Prefer Battery)'
    pv_charge_charge = 'PV Charge (Prefer Charge)'
    protect_batt = 'Protect Battery'
    min_charge = 'Min Charge'

######################################
#   Logging
######################################

try:
    os.makedirs(general_Config["log_path"])
    print("Logdir " + general_Config["log_path"] + " created")

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
    general_Config["log_path"] + general_Config["log_filename"],
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


def set_max_current(new_current):
    logging.debug("Adjusting Current to " + str(new_current) + " A")
    client.publish(
        "homie/Heidelberg-Wallbox/wallbox/max_current/set", new_current, 0, True
    )

WP_Out_Power = 0.0
WP_Out_Power_Queue = deque(maxlen=10)

def on_new_wp_out(client, userdata, message):
    global WP_Out_Power
    WP_Out_Power = float(message.payload.decode("utf-8"))
    WP_Out_Power_Queue.append(WP_Out_Power)
    logger.debug("MQTT  New WP_Out received: " + str(WP_Out_Power))

PV_In_Power = 0.0
PV_In_Queue = deque(maxlen=10)

def on_new_pv_in(client, userdata, message):
    global PV_In_Power
    PV_In_Power = float(message.payload.decode("utf-8"))
    PV_In_Queue.append(PV_In_Power)
    logger.debug("MQTT  New PV Power received: " + str(PV_In_Power))

soc_percent = 0.0

def on_new_soc_percent(client, userdata, message):
    global soc_percent
    soc_percent = float(message.payload.decode("utf-8"))
    logger.debug("MQTT  New SoC/Battery Percent received: " + str(soc_percent))

soc_power = 0.0

def on_new_soc_power(client, userdata, message):
    global soc_power
    soc_power = float(message.payload.decode("utf-8"))
    logger.debug("MQTT  New SoC/Battery Power received: " + str(soc_power))

wb_state = 0

def on_wallbox_state_change(client, userdata, message):
    global wb_state
    temp = message.payload.decode("utf-8")
    logger.info(f"MQTT   New Wallbox State: {temp}")
    try:
        wb_state = int(temp)
        logger.debug(f"MQTT  New Wallbox State: {str(wb_state)}")
    except ValueError:
        logger.error(f"Konnte Wallbox-Status nicht konvertieren: {temp}")

client.message_callback_add("vzlogger/data/chn2/raw", on_new_wp_out)
client.message_callback_add("emon/NodeHuawei/input_power", on_new_pv_in)
client.message_callback_add("emon/NodeHuawei/storage_state_of_capacity", on_new_soc_percent)
client.message_callback_add("emon/NodeHuawei/storage_charge_discharge_power", on_new_soc_power)
client.message_callback_add("homie/Heidelberg-Wallbox/$state", on_wallbox_state_change)

client.on_connect = on_connect

try:
    client.connect(mqtt_Config["host"],
                  int(mqtt_Config.get("port", 1883)),
                  30)
    logger.info(f"Erfolgreich mit MQTT-Broker {mqtt_Config['host']} verbunden")
except ConnectionRefusedError:
    logger.error(f"Verbindung zu MQTT-Broker {mqtt_Config['host']} verweigert")
except TimeoutError:
    logger.error(f"Zeitüberschreitung bei Verbindung zu MQTT-Broker {mqtt_Config['host']}")
except Exception as e:
    logger.error(f"Fehler bei MQTT-Verbindung: {str(e)}")

client.loop_start()


def deque_calc_avg(queue):
    if not queue or not isinstance(queue, deque):
        logger.warning("Ungültige Queue für Durchschnittsberechnung")
        return 0.0

    try:
        sum_calc = sum(queue)
        return sum_calc / len(queue)
    except (TypeError, ZeroDivisionError) as e:
        logger.error(f"Fehler bei Durchschnittsberechnung: {str(e)}")
        return 0.0


def roundDown(n):
    result = int("{:.0f}".format(n))
    if result < 6:
        return 0  # Unter Minimalwert - lieber stoppen
    elif result > 16:
        return 16  # Maximalen Wert zurückgeben
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
    set_max_current(0)

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
            logging.info("No Vehicle Connected ... skipping")
            logging.info(f"Current State is {current_state.value}")
            set_max_current(0)
            charging_car = False
            time.sleep(30)
            continue
        elif wb_state == 4:
            logging.info("Vehicle Connected without Charging request, Wallbox doesn't allow charging")
            charging_car = False
        elif wb_state == 5:
            logging.info("Vehicle Connected without Charging request, Wallbox allows charging")
        elif wb_state == 6:
            logging.info("Vehicle Connected with Charging request, Wallbox doesn't allow charging")
        elif wb_state == 7:
            logging.info("Vehicle Connected with Charging request, Wallbox allows charging")
        elif wb_state > 8:
            logging.info(f"Error state: {wb_state}")
            set_max_current(0)
            charging_car = False
            time.sleep(30)
            continue

        #Max Charge Mode .. ignore everything else
        if current_state == WallBoxMode.max_charge:
            logging.info("WallBoxMode: Max Charge with 16A")
            set_max_current(16)
            charging_car = True
            time.sleep(30)
            continue
        elif current_state == WallBoxMode.min_charge:
            logging.info("WallBoxMode: Min Charge with 6A")
            set_max_current(6)
            charging_car = True
            time.sleep(30)
            continue
        elif current_state == WallBoxMode.off:
            logging.info("WallBoxMode: off")
            set_max_current(0)
            charging_car = False
            time.sleep(30)
            continue
        elif current_state == WallBoxMode.protect_batt:
            logging.info("WallBoxMode: Protect Battery ... only start if battery is off")
            if soc_percent <= 2.0 and abs(soc_power) <= 10:
                logging.debug(f"Soc Percent is {str(soc_percent)} and SocPower is {str(soc_power)} ... starting")
                set_max_current(16)
                charging_car = True
            else:
                logging.debug(f"Soc Percent is {str(soc_percent)} and SocPower is {str(soc_power)} ... not starting")
                set_max_current(0)
                charging_car = False
            time.sleep(30)
            continue

        old_current = setting_ampere

        # Condition 1 : PV should produce more then minimum load
        pv_in = deque_calc_avg(PV_In_Queue)
        logging.debug(f"Queue length of PV_In_Queue is {str(len(PV_In_Queue))}, Avg is {str(pv_in)} W")

        # Need at least 3 values in deque for PV production
        if len(PV_In_Queue) <= 3:
            logger.info(f"Warming up ... PV_In_Queue is {str(len(PV_In_Queue))}, Avg is {str(pv_in)} W")
            # Execute every 10 seconds
            time.sleep(20)
            continue
        elif (len(PV_In_Queue) > 3 and pv_in > 1400.0):  # Getting a new PV Input Value every 10 seconds
            enough_pv = True
            logger.info("PV production enough for enable charging")
        else:
            enough_pv = False
            set_max_current(0)
            logger.info("Not enough PV production for charging ... stop charging")
            logger.info(f"pv_in is {str(pv_in)} W and queue length is {str(len(PV_In_Queue))}")

        # Condition 2: Check Battery
        if enough_pv is True and current_state == WallBoxMode.pv_charge_batt:
            if soc_percent == 100.0:
                load_battery = False
                logger.info(f"Battery is at {str(soc_percent)}% ... Fine to charge")
            elif charging_car is True and soc_percent < 95.0:
                logger.info(f"Battery is at {str(soc_percent)}% ... Charging is active, but battery dropped under 95% ... stopping")
                set_max_current(0)
                load_battery = True
                charging_car = False
            else:
                if soc_power > 0:
                    logger.info(f"Battery is at {str(soc_percent)}% ... Prefer charging battery then charge car, not starting to charge ... Battery injection is {str(soc_power)}")
                else:
                    logger.info(f"Battery is at {str(soc_percent)}% ... Prefer charging battery then charge car, not starting to charge ... Battery discharging is {str(soc_power)}")
                load_battery = True
        elif current_state == WallBoxMode.pv_charge_charge:
            load_battery = False
            logger.info(f"Battery is at {str(soc_percent)} % ... but ignore as WallBoxMode prefers charging")

        # Condition 3a: Check network injection resp. SoC injection to start charging, if not yet charging
        wp_out = deque_calc_avg(WP_Out_Power_Queue)
        if enough_pv is True and load_battery is False and charging_car is False:
            if current_state == WallBoxMode.pv_charge_batt and wp_out < -1380.0:
                charging_car = True
                setting_ampere = roundDown((-1) * wp_out / 230)
                logging.info(f"Starting PV Charge (Prefer Battery) the Car with {str(setting_ampere)} A as WP_Out is {str((-1) * wp_out)} W")
                set_max_current(setting_ampere)
                time.sleep(20)
                continue
            elif current_state == WallBoxMode.pv_charge_charge and soc_power > 1380:
                # WallBoxMode is to prefer charging rather then loading the SoC
                charging_car = True
                setting_ampere = roundDown(soc_power / 230)
                logging.info(f"Starting PV Charge (Prefer Charge) the Car with {str(setting_ampere)} A as SoC Power is {str(soc_power)} W")
                set_max_current(setting_ampere)
                time.sleep(20)
                continue
            else:
                charging_car = False
                logging.info(f"Not enough power left for starting to charge, WP_Out is {str(wp_out)} W")

        # Condition 3b: Car is charging; re-evaluate increase of decrease current
        if (charging_car is True
                and (current_state == WallBoxMode.pv_charge_charge or current_state == WallBoxMode.pv_charge_batt)):
            # Battery discharging
            if soc_power < 0:
                setting_ampere -= roundDown((-1) * soc_power / 230)
                soc_power_str = str(roundDown((-1) * soc_power / 230))
                logging.info(
                    f"Decrease Charge Power by {soc_power_str}A to {str(setting_ampere)}A as battery is discharging with {str(soc_power)} W")

            # Still injecting in the network ... increase by 1 Ampere if injection is more then 400W
            elif wp_out < -400.0:
                setting_ampere += 1
                logging.info(
                    f"Increasing Charge Power by 1A to {str(setting_ampere)}A as WP_Out is {str(wp_out)} W")

            elif current_state == WallBoxMode.pv_charge_charge and soc_power > 400:
                # SoC Injection is more then 1A * 230V = 230W ... increase by 1 Ampere
                setting_ampere += 1
                logging.info(
                    f"Increasing Charge Power by 1A to {str(setting_ampere)}A as SoC Power is {str(soc_power)} W")

            # Too much load, if retrieving from network
            elif wp_out > 50:
                setting_ampere -= max(1, math.ceil(roundDown(wp_out / 230)))
                wp_out_str = str(max(1, math.ceil(roundDown(wp_out / 230))))
                logging.info(
                    f"Decrease Charge Power by {wp_out_str}A to {str(setting_ampere)}A as WP_Out is {str(wp_out)} W")

            # Fix Charging Current
            if setting_ampere < 6:
                logging.info("under minimum charging load ... stop charging")
                setting_ampere = 0
                charging_car = False

            if setting_ampere > 16:
                logging.info("Reached max Loading Power ... resetting to 16 A")
                setting_ampere = 16

            if old_current != setting_ampere:
                logging.info(f"Set new Current to {str(setting_ampere)} A ... was {str(old_current)} A before")
                set_max_current(setting_ampere)

            else:
                logging.info(f"Keeping current of {str(setting_ampere)} A")

        # Execute every 10 seconds
        time.sleep(20)


try:
    logger.info("------------ Wallbox Regulator started ------------")
    time.sleep(5)  # Wait 5 Seconds for MQTT to Connect and Pull Messages
    loop()
except:
    logger.info("------------ Client exited ------------")
    set_max_current(0) # Setting Current back to 0
    client.disconnect()
    client.loop_stop()
finally:
    logger.info("------------ Stopping client ------------")
    set_max_current(0) # Setting Current back to 0
    client.disconnect()
    client.loop_stop()
