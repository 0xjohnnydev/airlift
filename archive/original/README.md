# AirLift: original PoC

This is the original, more flexible AirLift PoC. The
[current version](../../cleanroom/README.md) focuses on one temporary file in
`/var/tmp`. This archive keeps the original program, its target options, and
the research behind it.

AirTraffic moves media such as Books from a Mac to an iPhone. This PoC gives
AirTraffic a path that leads outside its intended Media area. It can create a
new file there without installing an iPhone app. In this document, **path
escape** means a media service reaches files beyond that area; it does not
mean kernel access or a full device takeover.

The original program can connect from a paired Mac over USB or Wi-Fi. Its
known-build list includes iOS 27.0 builds `24A435` and `24A5390f`. It warns
before running on other builds. The current version is limited to build
`24A437`.

## Where it can create files

The original research demonstrated creating a **new** file in each of these
directories:

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

Reading is indirect: AirTraffic moves a known file into Media, the Mac reads
it through AFC (Apple File Conduit), and AirTraffic moves it back. The original
PoC has not shown a working path to the MobileGestalt plist, a system
configuration file.

## Services involved

On the Mac, `MobileDevice.framework` connects to the phone and
`AirTrafficHost.framework` drives the Books sync. On the iPhone:

- `com.apple.streaming_zip_conduit` unpacks the ZIP archive.
- `com.apple.afc` gives the paired Mac access to the Media area.
- `com.apple.atc` (AirTrafficDevice) and the Books sync client process the
  Book entries.
- `ATLegacyAssetLink` and `ATAirlock` choose the file moves, which
  `NSFileManager` performs.

## Why the path escapes

The Books “Persistent ID” becomes the source path. The
`FileComplete.AssetPath` field becomes the destination path. For these Book
assets, the relevant logic in `-[ATAirlock processCompletedAsset:]` is:

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

`..` means “parent directory.” The source accepts those components without
checking where they lead. The destination check looks at a normalized path
string, but it does not check where a symbolic link actually points.

The ZIP first places a relative symbolic link inside its extraction folder.
AirTraffic moves that link under Media, changing what the relative link points
to. Its next move follows the link and places the canary outside Media. The
program reads the canary back, removes it, and restores any Books sync files
that were present before the run.

## Run the original version

From the repository root:

```sh
make -C archive/original
python3 archive/original/airlift.py
```

The program lists paired iPhones and asks you to choose one. Use `--device`
with the phone's UDID to skip the picker. The default destination is
`/var/mobile/Library/SpringBoard`; choose another directory with `--target`:

```sh
python3 archive/original/airlift.py --target /var/tmp
```

Add `--verbose` if you need helper details. The program creates a new,
randomly named canary file and removes it at the end.
