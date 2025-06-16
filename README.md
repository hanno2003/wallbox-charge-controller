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
  - **Protect Battery**: Only charge when battery is critically low (≤2%), so just use power from the grid

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

## Configuration

### Environment Variables

The application uses environment variables for configuration. These can be set in a `.env` file in the root directory of the project. Here's an example of the required variables:

```dotenv
# MQTT Connection
MQTT_HOST=192.168.0.10        # IP address of your MQTT broker
MQTT_PORT=1883                # Port of your MQTT broker

# Home Assistant Connection
HASS_HOST=https://homeassistant.example.com  # URL of your Home Assistant instance
HASS_TOKEN=your_long_lived_access_token      # Long-lived access token from Home Assistant

# Logging Configuration
LOG_DIR=./logs/               # Directory for log files
LOG_FILENAME=wallbox.log      # Name of the log file
```

To create a long-lived access token in Home Assistant:
1. Go to your Home Assistant profile (click your username in the bottom left)
2. Scroll to the bottom and find "Long-Lived Access Tokens"
3. Click "Create Token", give it a name (e.g., "Wallbox Controller"), and copy the token
4. Paste this token in your `.env` file

### MQTT Topic Structure

The controller subscribes to the following MQTT topics:

- `vzlogger/data/chn2/raw` - Power output measurement
- `emon/NodeHuawei/input_power` - PV power input
- `emon/NodeHuawei/storage_state_of_capacity` - Battery state of charge (%)
- `emon/NodeHuawei/storage_charge_discharge_power` - Battery charge/discharge power
- `homie/Heidelberg-Wallbox/$state` - Wallbox state
- `homie/Heidelberg-Wallbox/wallbox/akt_verbrauch` - Current wallbox consumption

The controller publishes to:
- `homie/Heidelberg-Wallbox/wallbox/max_current/set` - To set the charging current

### Docker Configuration

If you're using Docker, make sure to update the `docker-compose.yml` file with the correct paths:

```yaml
services: 
  wallbox_regulator:
    build:
      context: .  # Use relative path to the current directory
      dockerfile: Dockerfile
    container_name: 'wallbox-charge-controller'
    restart: unless-stopped
    volumes:
      - /etc/localtime:/etc/localtime:ro
      - ./logs:/app/logs  # Mount the logs directory
    env_file:
      - .env  # Load environment variables from .env file
    networks:
      - default
```

## Installation

1. Clone the repository:
   ```bash
   git clone https://github.com/yourusername/wallbox-charge-controller.git
   cd wallbox-charge-controller
   
2. Build docker image and run it 
   ```bash
   docker compose build -t .
   docker compose up
```

## Troubleshooting

### Common Issues

1. **MQTT Connection Problems**
   - Check if your MQTT broker is running: `mosquitto_sub -h [MQTT_HOST] -t "#" -v`
   - Verify MQTT credentials and connection settings in your `.env` file
   - Ensure the required MQTT topics exist and are being published to

2. **Home Assistant Integration Issues**
   - Verify your Home Assistant token is valid and not expired
   - Check that the Home Assistant URL is correct and accessible
   - Make sure the `input_select.wallbox_charge_mode` entity exists in Home Assistant

3. **Charging Not Starting**
   - Check the wallbox state in the logs (should be state 4 or higher for charging)
   - Verify PV production is sufficient (needs >1400W for charging to start)
   - Check battery state if in "Prefer Battery" mode (needs >98.5% SoC)

4. **Log Errors**
   - Check the log files in your configured log directory
   - Look for error or warning messages
   - Verify log file permissions if running in Docker

### Viewing Logs

You can view the logs using:

```bash
# View the latest logs
tail -f logs/mein_chargecontroller.log

# View compressed archive logs
zcat logs/mein_chargecontroller.log.2025-06-09.gz
```

## Maintenance

### Updating the Controller

1. Pull the latest changes:
   ```bash
   git pull origin main
   ```

2. Rebuild and restart the Docker container:
   ```bash
   docker compose down
   docker compose build
   docker compose up -d
   ```

### Log Rotation

Logs are automatically rotated daily and compressed with gzip. Old logs are stored in the format `[LOG_FILENAME].[DATE].gz`. You may want to periodically clean up older log files to save disk space:

```bash
# Remove log files older than 30 days
find logs/ -name "*.gz" -mtime +30 -delete
```

## License

This project is licensed under the MIT License - see the [LICENSE](LICENSE) file for details.
