# NyxHUD

A lightweight, modular desktop HUD for Wayland.

NyxHUD is a minimal, offline-first information panel written around a
modular architecture. Each data source is an independent POSIX shell
collector, while a lightweight Python renderer displays the information
on screen.

![NyxHUD](screenshots/desktop.png)

The project was designed around simplicity, auditability and low
resource usage.

## Features

-   Wayland native
-   Modular collector architecture
-   POSIX shell collectors
-   Lightweight Python renderer
-   Offline-first design
-   Minimal dependencies
-   RSS support through modules
-   Easy to extend
-   Low CPU and memory usage

## Project Structure

``` text
nyxhud/
├── main/
│   ├── collectors/
│   ├── renderer/
│   ├── modules/
│   └── cache/
├── start.sh
└── README.md
```

## Dependencies

-   Python 3
-   GTK3 (PyGObject)
-   Wayland
-   labwc
-   foot
-   POSIX shell

Optional tools:

-   curl
-   iproute2
-   procps-ng
-   lm_sensors

## Installation

``` sh
git clone git@github.com:fm4lloc/nyxhud.git
cd nyxhud
./start.sh
```

## Modules

Collectors are located in `main/collectors/` and can be extended by
adding new POSIX shell scripts.

## Philosophy

-   KISS
-   Modular
-   Offline First
-   Easy to Audit
-   Minimal Dependencies
-   Predictable Behavior

## License

GPL-3.0-or-later
