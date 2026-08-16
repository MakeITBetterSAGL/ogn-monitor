# Changelog

All notable changes to OGN Monitor are documented in this file.

The project follows [Semantic Versioning](https://semver.org/).

## [1.2.0] - 2026-08-16

### Added

- Added a responsive Statistics page with selectable ranges from 2 hours to 1 year.
- Added charts for traffic rate, aircraft seen, reception range, signal quality, altitude, speed and protocol traffic.
- Added a shared Metric / Imperial selector for the Monitor and Statistics pages.
- Added high-resolution Replay telemetry with point timing, altitude, speed and interval summaries.
- Added anonymized desktop and mobile Statistics screenshots generated from synthetic data.

### Changed

- Increased the Replay display limit while retaining browser-side protection for exceptionally large sessions.
- Improved Statistics chart grids, dual vertical axes, legends and mobile readability.
- Kept Receiver health based on generic Linux resource and standard service checks.

### Fixed

- Corrected aircraft icon orientation by using the proper 90-degree heading offset.

### Related issues

- [#3 - Aircraft icon heading offset](https://github.com/MakeITBetterSAGL/ogn-monitor/issues/3), reported by [@whallmann](https://github.com/whallmann).

## [1.1.2] - 2026-08-15

### Fixed

- Store altitude directly in metres as returned by `aprslib`, avoiding a duplicate feet-to-metres conversion.
- Store speed directly in km/h as returned by `aprslib`, avoiding a duplicate knots-to-km/h conversion.
- Prevent packets without a usable position from blocking newer packets in the parser queue.
- Count newly inserted positions accurately.

### Changed

- Added a persistent parser checkpoint so every packet is examined once and busy receivers can keep progressing.

### Related issues

- [#1 - Speed of Object much to high](https://github.com/MakeITBetterSAGL/ogn-monitor/issues/1), reported by [@whallmann](https://github.com/whallmann).
- [#2 - After some days, Map shows no Objects.](https://github.com/MakeITBetterSAGL/ogn-monitor/issues/2), reported by [@whallmann](https://github.com/whallmann).

### Commits

- [`07e92ee`](https://github.com/MakeITBetterSAGL/ogn-monitor/commit/07e92ee0ff9fde6a89837936a8f9a459f32a1bb6) Fix parser queue progress and APRS units

## [1.1.1] - 2026-08-12

### Added

- Added anonymized desktop and mobile dashboard screenshots.
- Documented that screenshots use synthetic traffic and an approximate public map centre.

### Commits

- [`7acb56c`](https://github.com/MakeITBetterSAGL/ogn-monitor/commit/7acb56c2c4b5e30a6b9381f8bf0edb40f7daf5d0) Document anonymized dashboard screenshots
- [`15d36c1`](https://github.com/MakeITBetterSAGL/ogn-monitor/commit/15d36c16f9133286decdb8957291268b2d96a2de) Add anonymized dashboard screenshots

## [1.1.0] - 2026-08-11

### Added

- Added a daily maximum-distance summary to History.
- Added compact session cards, aircraft-detail switching, sorting and pagination.
- Added responsive replay controls and improved map controls.
- Added the project footer and repository link.

### Changed

- Redesigned the dashboard for a consistent compact layout on desktop and mobile.
- Improved History day cards, filters, session grids and responsive column counts.
- Reduced unnecessary vertical scrolling throughout the dashboard.
- Improved active-aircraft behaviour when no recent traffic is available.

### Commits

- [`dcc6d7d`](https://github.com/MakeITBetterSAGL/ogn-monitor/commit/dcc6d7d53a3b48f334483568b2fbfb4dda972541) Update compact dashboard layout
- [`5e4f924`](https://github.com/MakeITBetterSAGL/ogn-monitor/commit/5e4f9246873542b9dc34f1dcc79503acdac64063) Improve public dashboard interactions
- [`062c35f`](https://github.com/MakeITBetterSAGL/ogn-monitor/commit/062c35ffb1410281a5ab5d0c59ae0886f67f480e) Refresh responsive dashboard styles
- [`66661ed`](https://github.com/MakeITBetterSAGL/ogn-monitor/commit/66661ed5f22670a0413fbbac7126146405a57b46) Add daily distance summary

## [1.0.0] - 2026-08-07

### Added

- Initial public release of OGN Monitor.
- Live receiver statistics, active-aircraft map and recent tracks.
- Daily History with flight sessions and map replay.
- Coverage maps, receiver health and Raspberry Pi service information.
- Raspberry Pi installation and configuration guide.
- Neutral example station settings suitable for a public repository.

### Commits

- [`de12e6b`](https://github.com/MakeITBetterSAGL/ogn-monitor/commit/de12e6bd29e9130cc50a0bb0c0e48447ed4bdf6e) Add Raspberry Pi installation guide
- [`8063ad2`](https://github.com/MakeITBetterSAGL/ogn-monitor/commit/8063ad2e20b483d8ebf55068aef6164fb4f1bb7b) Use neutral example station settings
- [`38b2b95`](https://github.com/MakeITBetterSAGL/ogn-monitor/commit/38b2b95893c6e2477e07669156ce08b9a1bb75a6) Initial public release
