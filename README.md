# AirLift

AirLift demonstrates an AirTraffic path escape on a paired iPhone. The current
proof of concept is in [`cleanroom/`](cleanroom/README.md). It writes a fresh
canary file to `/var/tmp`, reads the exact bytes back, and cleans up. Its tested
target is iPhone 18 Pro (`iPhone19,2`) on iOS 27.0 build `24A437` while connected by USB.

```sh
make
python3 cleanroom/poc.py --device <iPhone UDID>
```

The original PoC, its helper sources, build file, and full research notes remain
in [`archive/original/`](archive/original/README.md). To build and run that
version from the repository root:

```sh
make -C archive/original
cd archive/original
./airlift.py
```

The archive retains the original PoC's options and target scope. Its README
records the original research and verified paths. Build files in either
directory are ignored by Git.
