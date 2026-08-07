# Installation guide for Raspberry Pi

This guide installs OGN Monitor on a new Raspberry Pi. It assumes that the Raspberry Pi is already receiving and decoding OGN traffic.

OGN Monitor does not control the SDR radio and does not replace the OGN receiver software. It reads the APRS text stream produced by `ogn-decode`, normally available on TCP port `50001`.

## 1. What you need

- a Raspberry Pi running a current Raspberry Pi OS release;
- an RTL-SDR and antenna suitable for your local OGN frequency;
- a working OGN receiver/decoder;
- network access from another computer;
- the receiver name, latitude, longitude and local time zone.

A Raspberry Pi 3 is sufficient. A Pi 4 or Pi 5 provides more headroom but is not required.

## 2. Prepare Raspberry Pi OS

Use Raspberry Pi Imager to install Raspberry Pi OS Lite. In the Imager settings:

1. choose a hostname;
2. create a non-default user and a strong password;
3. configure Wi-Fi if Ethernet is not used;
4. enable SSH;
5. set the correct locale and time zone.

Boot the Raspberry Pi and connect over SSH:

```sh
ssh your-user@raspberry-pi-address
```

Update the operating system and install the required tools:

```sh
sudo apt update
sudo apt full-upgrade -y
sudo apt install -y git python3 python3-venv curl netcat-openbsd
sudo reboot
```

Reconnect after the reboot.

## 3. Install and verify the OGN decoder

Install and configure the OGN receiver software before installing this dashboard. Follow the Open Glider Network documentation:

- [OGN receiver downloads and Raspberry Pi images](https://wiki.glidernet.org/downloads)
- [OGN manual installation guide](https://wiki.glidernet.org/wiki%3Amanual-installation-guide)
- [OGN receiver FAQ](https://wiki.glidernet.org/ogn-receiver-faq)

Receiver images and decoder packages can differ in layout. Configure the receiver name, exact coordinates, altitude, radio band and SDR frequency correction according to the OGN documentation and your hardware.

Verify that the decoder is listening on the standard local port:

```sh
nc -v 127.0.0.1 50001
```

A working decoder prints status or APRS lines. Press `Ctrl+C` to exit. If the connection is refused, fix the OGN receiver before continuing. If your decoder uses another host or port, note those values for the `.env` file.

## 4. Download OGN Monitor

From the user's home directory:

```sh
cd ~
git clone https://github.com/MakeITBetterSAGL/ogn-monitor.git
cd ogn-monitor
```

Run the assisted installer as the normal user, not through `sudo`:

```sh
./scripts/install.sh
```

The installer:

- creates a private Python virtual environment in `.venv`;
- installs Flask, Gunicorn and APRS parsing dependencies;
- creates `.env` from the safe example when it does not exist;
- installs systemd service definitions;
- leaves the services stopped until configuration is complete.

## 5. Configure the station

Open the configuration file:

```sh
nano .env
```

Set the station-specific values:

```dotenv
OGN_STATION_NAME="MyStation"
OGN_STATION_LATITUDE="0.000000"
OGN_STATION_LONGITUDE="0.000000"
OGN_TIMEZONE="UTC"

OGN_DECODER_HOST="127.0.0.1"
OGN_DECODER_PORT="50001"
```

Replace the example values with the receiver's real name and coordinates. Use decimal degrees: north and east are positive, south and west are negative.

Use an [IANA time-zone name](https://en.wikipedia.org/wiki/List_of_tz_database_time_zones), for example `Europe/London`, `Europe/Rome`, `America/New_York` or `Australia/Sydney`. The time zone controls daily history boundaries and displayed dates.

Save with `Ctrl+O`, press `Enter`, and exit with `Ctrl+X`.

The remaining settings can normally keep their defaults:

```dotenv
OGN_ACTIVE_MINUTES="10"
OGN_TRACK_MINUTES="30"
OGN_ONLINE_SECONDS="120"
OGN_SESSION_GAP_MINUTES="20"
OGN_PARSER_POLL_SECONDS="2"
OGN_WEB_HOST="0.0.0.0"
OGN_WEB_PORT="5000"
```

## 6. Download public aircraft metadata

Run the initial OGN Devices Database update:

```sh
./scripts/update-ogn-ddb.sh
```

The daily systemd timer installed later keeps this local public database up to date. OGN Monitor respects the identification setting contained in the source database.

## 7. Start OGN Monitor

Enable and start all components:

```sh
sudo systemctl enable --now \
    ogn-collector.service \
    ogn-parser.service \
    ogn-monitor.service \
    ogn-ddb-update.timer
```

Check their status:

```sh
systemctl --no-pager --full status \
    ogn-collector.service \
    ogn-parser.service \
    ogn-monitor.service
```

All three services should show `active (running)`. Confirm that the web API responds:

```sh
curl http://127.0.0.1:5000/api/stats
```

Find the Raspberry Pi address:

```sh
hostname -I
```

On another device connected to the same network, open:

```text
http://raspberry-pi-address:5000
```

The page may correctly show **No active traffic right now** when the decoder is online but no aircraft have recently been received.

## 8. Check logs

Follow the dashboard log:

```sh
journalctl -u ogn-monitor.service -f
```

Collector and parser logs:

```sh
journalctl -u ogn-collector.service -u ogn-parser.service -f
```

Press `Ctrl+C` to stop following a log.

## 9. Updating

From the project directory:

```sh
cd ~/ogn-monitor
git pull --ff-only
.venv/bin/pip install -r requirements.txt
sudo systemctl restart ogn-collector ogn-parser ogn-monitor
```

Read the release notes before updating in case a future version contains additional migration steps.

## 10. Backups and migration

The configuration is stored in `.env`. Historical packets and positions are stored in `database/ogn.sqlite3`.

For a simple consistent backup:

```sh
cd ~/ogn-monitor
sudo systemctl stop ogn-monitor ogn-parser ogn-collector
cp .env ~/ogn-monitor.env.backup
cp database/ogn.sqlite3 ~/ogn-monitor.sqlite3.backup
sudo systemctl start ogn-collector ogn-parser ogn-monitor
```

Store those two backup files somewhere other than the Raspberry Pi. They are deliberately excluded from Git.

## 11. Troubleshooting

### The portal does not open

Check the service and local port:

```sh
systemctl status ogn-monitor.service
curl http://127.0.0.1:5000/api/stats
ss -ltn | grep ':5000'
```

Also verify that the client is on the same network and that no firewall blocks TCP port 5000.

### Receiver offline

Verify the decoder stream:

```sh
nc -v 127.0.0.1 50001
```

Then confirm that `OGN_DECODER_HOST` and `OGN_DECODER_PORT` in `.env` match the decoder.

### No aircraft or history

Check whether APRS lines are present on port 50001 and inspect the collector/parser logs. An empty database is expected until valid aircraft packets are received.

### Map tiles do not load

The map providers and Leaflet library are external services. Confirm that the browser has Internet access and inspect browser content-blocking or DNS settings.

### Aircraft information is unavailable

Run:

```sh
./scripts/update-ogn-ddb.sh
systemctl status ogn-ddb-update.timer
```

Metadata is shown only for records publicly identified in the OGN Devices Database.

## 12. Security scope

The public edition deliberately serves plain HTTP on port 5000 and has no login page. It is intended for a trusted local network.

Do not forward port 5000 directly from an Internet router. Public Internet deployment requires a separately maintained reverse proxy, HTTPS and an explicit security policy; those components are outside this project's scope.
