# AirLift

AirLift shows a path escape in the iPhone's AirTraffic media-sync service.
AirTraffic normally handles files inside the phone's Media area. This PoC makes
it create one new file outside that area, in `/var/tmp`, then reads the file
back and removes it. It runs from a Mac paired with the iPhone; no iPhone app
is needed.

## Run the current PoC

You need a Mac with Python 3 and Xcode. The iPhone must trust the Mac and be
connected by USB. The current PoC accepts iOS 27.0 build `24A437`. It has been
used on an iPhone 18 Pro (`iPhone19,2`).

From the repository root:

```sh
xcrun devicectl list devices
make
python3 cleanroom/poc.py --device <UDID>
```

The first command shows the phone's **UDID** (its device identifier). Replace
`<UDID>` with that value. The run prints `exactBytesRecovered: true` when the
phone returns the same file contents and `cleanupComplete: true` when it
removes its temporary files. If cleanup cannot finish, the output names a
`recoveryFiles` directory on the Mac.

## Original PoC and technical details

The [current PoC](cleanroom/README.md) focuses on one new file in `/var/tmp`.
The [original PoC and research notes](archive/original/README.md) cover more
target directories and explain the path handling in detail. The original
version remains runnable:

```sh
make -C archive/original
python3 archive/original/airlift.py
```

The original script offers a device picker and a `--target` option. Its
default destination is `/var/mobile/Library/SpringBoard`.
