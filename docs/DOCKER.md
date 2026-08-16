# Docker installation

This setup runs OGN Monitor as one unprivileged application container. The OGN
decoder, SDR drivers and radio hardware remain on the host. The container starts
the collector, parser and Gunicorn web server and stores runtime data in a named
Docker volume.

## Requirements

- Docker Engine with Docker Compose;
- an existing OGN decoder exposing APRS text over TCP;
- network access from the container to the decoder TCP port;
- an `amd64` or `arm64` Linux host.

The container does not require privileged mode, USB access, the Docker socket,
or access to the host's `/proc` and `/sys` filesystems.

## Configure

Clone the repository and create the local Docker configuration:

```sh
git clone https://github.com/MakeITBetterSAGL/ogn-monitor.git
cd ogn-monitor
cp docker.env.example docker.env
```

Edit `docker.env` and set the station name, coordinates, time zone, decoder host
and decoder port. The file is ignored by Git.

The default decoder host is `host.docker.internal`. Compose maps that name to
the Docker host on Linux. The decoder must accept connections from the Docker
bridge; a service bound exclusively to `127.0.0.1` is not reachable from a
bridge container. Keep the decoder port private to the host and local network.

## Start

```sh
docker compose --env-file docker.env up --build -d
docker compose ps
docker compose logs -f ogn-monitor
```

Open `http://<docker-host-address>:5000`. Change `OGN_HTTP_PORT` in
`docker.env` if port 5000 is already used on the host. Keep using
`--env-file docker.env` in Compose commands so this host-port setting is also
applied to the Compose configuration.

## Application health

Docker installations show **Application health** rather than Raspberry Pi
Receiver health. It reports only values the container can measure accurately:

- decoder-feed connectivity;
- collector and parser process state;
- parser backlog;
- database availability and size;
- free space in the persistent data volume;
- application uptime.

Host CPU load, memory, temperature, systemd services and SD-card status are not
shown. Exposing them would require host-specific or privileged access and would
make the image less portable and less secure.

Docker also checks `/api/health`. The container is considered healthy when the
web application, database, collector and parser are available. A disconnected
external decoder is shown in the dashboard but does not restart the container.

## Data and updates

The `ogn-data` volume contains:

- `ogn.sqlite3` and its SQLite WAL files;
- `ogn-ddb.json`, refreshed daily when network access is available.

Rebuilding or replacing the application container does not remove this volume.
To update the application:

```sh
git pull
docker compose --env-file docker.env up --build -d
```

To stop the application without removing its data volume:

```sh
docker compose down
```

Do not add `--volumes` unless you intentionally want to remove all recorded
OGN Monitor data.

## Troubleshooting

If the dashboard reports **Decoder feed: Unavailable**, verify the endpoint
from the Docker host and confirm that the decoder listens on an address the
Docker bridge can reach. Then check `OGN_DECODER_HOST` and `OGN_DECODER_PORT` in
`docker.env`.

If the container is unhealthy, inspect:

```sh
docker compose ps
docker compose logs --tail=200 ogn-monitor
docker inspect --format '{{json .State.Health}}' ogn-monitor
```
