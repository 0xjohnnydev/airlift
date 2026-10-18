# airlift

### An AirTraffic sandbox escape for iOS 27.0.

This is a simple proof of concept for developers and security researchers.

If you are worried about 🔥🪲4⃣☁️ (burning bugs for clout) - there are always
more bugs <sub><img src="./archive/original/assets/trollface.svg" width="22" height="18" alt="trollface"></sub>

AirTraffic syncs media, including Books, from a Mac to an iPhone. AirLift gives
it paths that lead outside its intended Media area. The result is a new file
outside Media, made without installing an iPhone app. Here, “sandbox escape”
describes AirTraffic crossing that file boundary; it does not mean kernel
access or control of the whole phone.

The [current PoC](cleanroom/README.md) is the default. It makes one temporary
file in `/var/tmp`, reads the exact bytes back, and cleans up. The
[original PoC](archive/original/README.md) and its research notes remain in
`archive/original/`. It supports more target directories. The two versions
use separate code but demonstrate the same path-handling flaw.

## Run the current PoC

You need a Mac with Python 3 and Xcode, plus a paired iPhone that trusts the
Mac and is connected by USB. This version accepts iOS 27.0 build `24A437` and
has been used on an iPhone 18 Pro (`iPhone19,2`). From the repository root:

```sh
xcrun devicectl list devices
make
python3 cleanroom/poc.py --device <UDID>
```

The first command shows the phone's **UDID** (device identifier). Use that
value in the last command. The result includes `exactBytesRecovered: true`
when the phone returns the same file contents and `cleanupComplete: true`
when the PoC removes its temporary files. If cleanup cannot finish, the
result names a `recoveryFiles` directory on the Mac. The
[current PoC guide](cleanroom/README.md) explains each step and output field.

## What the original PoC reached

The original PoC runs from a paired Mac over USB or Wi-Fi. It was tested on
iOS 27.0 RC (`24A435`); its source also lists `24A5390f` as a known build and
warns before running on other builds. Fresh-file writes were confirmed in
these directories:

```text
/var/mobile
/var/mobile/Documents
/var/mobile/Library
/var/mobile/Library/Preferences
/var/mobile/Library/Caches
/var/mobile/Library/SpringBoard
/var/mobile/Library/SMS
/var/mobile/Library/Safari
/var/mobile/Containers
/var/mobile/Containers/Data/Application
/var/mobile/Containers/Shared/AppGroup
/var/tmp
```

Reads are indirect: AirTraffic moves a known file into Media, the Mac reads
it through **AFC** (Apple File Conduit), and AirTraffic moves it back. The
original PoC has not shown a working path to the MobileGestalt plist, a
system configuration file.

## Components

The original research maps the services and file-move code involved. The
arrows show the parts, not a literal call sequence:

```text
──────────────── macOS ────────────────
MobileDevice.framework
↓
AirTrafficHost.framework

───────────────── iOS ─────────────────
com.apple.streaming_zip_conduit
↓
com.apple.afc
↓
com.apple.atc / AirTrafficDevice
↓
Books sync client
↓
ATLegacyAssetLink
↓
ATAirlock
↓
NSFileManager
```

`MobileDevice.framework` connects to the iPhone. `AirTrafficHost.framework`
drives the Books sync. On the phone, StreamingZip unpacks the ZIP archive,
AFC gives the Mac access to Media, and AirTraffic's Books path reaches the
file moves performed by `NSFileManager`.

## Why the path escapes

For these Book assets, the Books “Persistent ID” becomes the source path and
`FileComplete.AssetPath` becomes the destination path. A **symbolic link**
(a file-system pointer) can make a safe-looking path lead elsewhere. The
effective logic in `-[ATAirlock processCompletedAsset:]` is:

```objc
// Books "Persistent ID" reaches asset.identifier without path validation.
NSString *source =
    [@"/var/mobile/Media/Airlock/Book"
        stringByAppendingPathComponent:asset.identifier];

// FileComplete.AssetPath controls asset.path.
NSString *destination =
    [[@"/var/mobile/Media/"
        stringByAppendingPathComponent:asset.path]
        stringByStandardizingPath];

// This checks the path string, not where a symlink resolves.
if (![destination hasPrefix:@"/var/mobile/Media/"])
    return;

// The source is unchecked and the destination follows ancestor symlinks.
[fileManager moveItemAtPath:source
                     toPath:destination
                      error:&error];
```

`..` means “parent directory.” The source accepts those components from a
Books asset identifier without checking where they lead. The destination
check examines the path string, but it does not check where the link resolves.

StreamingZip first accepts a relative symbolic link while it still points
inside its extraction directory. AirTraffic moves the link under Media,
changing what the relative path points to. A second move follows it and
writes the canary outside Media. A **canary** is short, random text used to
show where the write landed. The original PoC reads it back, removes it, and
restores any Books sync files that were present before the run.

## Run the original PoC

From the repository root:

```sh
make -C archive/original
python3 archive/original/airlift.py

# Choose another destination.
python3 archive/original/airlift.py --target /var/mobile/Library/Safari
```

The original program lists paired iPhones and asks which one to use. Pass
`--device <UDID>` to skip the picker. Its default destination is
`/var/mobile/Library/SpringBoard`. Add `--verbose` for helper details on
successful runs. The [archived README](archive/original/README.md) keeps the
original project text and commands.
