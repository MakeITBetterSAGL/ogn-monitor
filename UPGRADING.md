# Upgrading OGN Monitor

This guide covers upgrades between published OGN Monitor releases. Read the
[release notes](https://github.com/MakeITBetterSAGL/ogn-monitor/releases) and
the version-specific notes below before starting.

Receiver settings are kept in `.env` on native installations and in
`docker.env` on Docker installations. Both files are ignored by Git and are not
replaced during a normal update. Recorded data is also retained.

## Before upgrading

1. Note the version currently installed:

   ```sh
   git describe --tags --always
   ```

2. Make sure your configuration file is present and keep a copy outside the
   repository if it contains settings you cannot easily recreate.
3. Check the release notes for new environment variables, services or manual
   migration steps.
4. If the installation contains local source-code changes, commit or preserve
   them before updating. The commands below expect a clean working tree.

OGN Monitor currently requires no manual database-schema migration. The
application creates compatible tables and columns when it starts.

## Native Raspberry Pi installation

Run the following commands from the project directory:

```sh
cd ~/ogn-monitor
git status --short
git pull --ff-only
./scripts/install.sh
sudo systemctl enable --now ogn-ddb-update.timer ogn-retention.timer
sudo systemctl restart ogn-collector ogn-parser ogn-monitor
```

The installer refreshes the Python environment and systemd unit files without
overwriting `.env` or the SQLite database.

Verify the result:

```sh
systemctl --no-pager --full status \
  ogn-collector ogn-parser ogn-monitor \
  ogn-ddb-update.timer ogn-retention.timer
curl --fail http://127.0.0.1:5000/api/health
journalctl -u ogn-monitor -n 50 --no-pager
```

## Docker installation

Run the following commands from the project directory:

```sh
git status --short
git pull --ff-only
docker compose --env-file docker.env up --build -d
```

Compose recreates the application container but retains `docker.env` and the
named `ogn-data` volume.

Verify the result:

```sh
docker compose --env-file docker.env ps
docker compose --env-file docker.env logs --tail=100 ogn-monitor
curl --fail http://127.0.0.1:5000/api/health
```

Replace port `5000` in the last command if `OGN_HTTP_PORT` uses a different
host port.

Do not use `docker compose down --volumes` during an update: that option removes
the volume containing the recorded data.

## Rolling back

First identify the previous release in the
[release list](https://github.com/MakeITBetterSAGL/ogn-monitor/releases). The
example below rolls back the application files to `v1.3.0`:

```sh
git fetch --tags
git checkout v1.3.0
```

For a native installation, rerun `./scripts/install.sh` and restart the three
application services. For Docker, run:

```sh
docker compose --env-file docker.env up --build -d
```

This produces a detached Git checkout. After diagnosing the problem, return to
the current release with:

```sh
git checkout main
git pull --ff-only
```

Database changes are designed to remain backward compatible, but data deleted
by a retention policy cannot be restored by rolling back the application.

## Version-specific notes

### Upgrading to v1.4.0

Version 1.4.0 adds recording filters, scheduled retention, explicit ADS-B
classification and Replay time-window controls.

- Native installations must rerun `./scripts/install.sh` and enable
  `ogn-retention.timer` as shown above.
- Docker installations receive the retention worker when the image is rebuilt.
- `OGN_RETENTION_DAYS` defaults to `365`. Set the desired value in `.env` or
  `docker.env` before enabling the retention timer. Retention permanently
  removes older packets and positions.
- `OGN_FILTER_MAX_RADIUS_KM`, `OGN_FILTER_MIN_ALTITUDE_M` and
  `OGN_FILTER_MAX_ALTITUDE_M` are optional. Leave them blank to preserve the
  previous recording behaviour.
- Radius and altitude filters apply only to new positions; existing history is
  not removed.
- Altitude limits use metres above mean sea level (AMSL).

No manual database migration is required for v1.4.0.
