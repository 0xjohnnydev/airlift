# airlift

### An AirTraffic sandbox escape for iOS 27.0.

This is a simple proof-of-concept for developers and security researchers.

If you are worried about 🔥🪲4⃣☁️ (burning bugs for clout) - there are always more bugs <sub><img src="./assets/trollface.svg" width="22" height="18" alt="trollface"></sub>

AirTraffic syncs media, including Books, from a Mac to iOS. airlift abuses that path to read and write files outside AirTraffic's intended directory scope. It runs from a paired Mac over Wi-Fi or USB, with no iOS app required. Tested on iOS 27.0 RC (24A435); it should also work on iOS 27.0 final (24A437). Other iPhone builds are allowed with a warning.

#### Verified scope

Fresh-file writes were confirmed in the following directories:

<table>
<tr><td>

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

</td></tr>
</table>

Reads are indirect: a known file is moved into Media, read through AFC, and
moved back.

As of now, this does **not** work on the MobileGestalt plist.

#### Components

<table>
<tr><td>

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

</td></tr>
</table>

#### ATAirlock path validation

Effective logic in `-[ATAirlock processCompletedAsset:]` for these Book assets:

<table>
<tr><td>

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

</td></tr>
</table>

The unchecked source accepts `..` components from a Books asset identifier.
StreamingZip accepts the relative symlink while it is still contained in its
extraction directory. The first move relocates it below Media; the second uses
it as part of the destination and writes the payload outside Media.

The included PoC writes a random canary, verifies it, and removes it. Existing
Books sync files are preserved and restored after the run.

#### Build and run

airlift lists compatible paired iPhones and asks which one to use. Pass a
UDID with `--device` to skip the prompt. Failed runs include helper diagnostics;
pass `--verbose` to include them on successful runs too.

<table>
<tr><td>

```sh
make
./airlift.py

# Choose another destination.
./airlift.py --target /var/mobile/Library/Safari
```

</td></tr>
</table>

The default destination is `/var/mobile/Library/SpringBoard`.
