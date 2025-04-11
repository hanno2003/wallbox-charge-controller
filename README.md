# Wallbox Charge Controller

This project provides an intelligent charging controller for Heidelberg Wallbox EV chargers that optimizes charging based on solar production, battery state, and home power consumption.

## Overview

The Wallbox Charge Controller is a Python application that dynamically adjusts electric vehicle charging current to maximize use of solar energy while respecting battery priorities and minimizing grid power usage. It integrates with Home Assistant for mode control and communicates with various components via MQTT.

## Features

- **Multiple charging modes**:
  - **Off**: No charging permitted
  - **Max Charge**: Charge at maximum power (16A) regardless of other factors
  - **Min Charge**: Charge at minimum power (6A) regardless of other factors
  - **PV Charge (Prefer Battery)**: Use excess solar, prioritize battery charging over car charging
  - **PV Charge (Prefer Charge)**: Use excess solar, prioritize car charging over battery charging
  - **Protect Battery**: Only charge when battery is critically low (≤2%)

- **Dynamic regulation**:
  - Smooths power fluctuations using rolling averages
  - Automatically adjusts charging current based on real-time conditions
  - Ensures charging current stays within safe limits (6A-16A)
  - Prioritizes renewable energy usage

- **Monitoring and logging**:
  - Comprehensive logging system with daily rotation and compression
  - Detailed status reporting through logs
  - Real-time response to changing power conditions

## Requirements

- A Heidelberg Wallbox with MQTT connectivity
- Home Assistant installation
- MQTT broker
- Huawei Solar PV system with monitoring
- Home battery system (optional)
- Power monitoring system that publishes to MQTT

## Installation

1. Clone the repository:
   ```bash
   git clone https://github.com/yourusername/wallbox-charge-controller.git
   cd wallbox-charge-controller
   
2. Build docker image and run it 
   ```bash
   docker compose build -t .
   docker compose up

