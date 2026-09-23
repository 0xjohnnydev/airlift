# airlift

### An AirTraffic sandbox escape for iOS 27.0.

This is a simple proof-of-concept for developers and security researchers.

If you are worried about 🔥🪲4⃣☁️ (burning bugs for clout) - there are always more bugs <sub><img src="./assets/trollface.svg" width="22" height="18" alt="trollface"></sub>

AirTraffic syncs media, including Books, from a Mac to iOS. airlift abuses that path to read and write files outside AirTraffic's intended directory scope. It runs from a paired host over Wi-Fi or USB, with no iOS app required. Tested on iOS 27.0 RC (24A435) and iOS 27.0 final (24A437). Other iPhone builds are allowed with a warning.

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
────────────── macOS host ──────────────
MobileDevice.framework
↓
AirTrafficHost.framework

───────────── Windows host ─────────────
pymobiledevice3 + Apple Mobile Device Support
↓
AirTrafficHost.dll

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

#### Windows

The Windows host uses Apple Mobile Device Support's `AirTrafficHost.dll` and
`CoreFoundation.dll` for the AirTraffic exchange, plus `pymobiledevice3` for
paired-device discovery, StreamingZip, and AFC. It implements the same fresh,
self-cleaning canary as the macOS host; it is not a generic read/write tool.

Install 64-bit Apple Mobile Device Support (the desktop iTunes installer is
one source), Python 3.11 or newer, and the Python dependency:

```powershell
pip install -r requirements-windows.txt
python airlift_windows.py

# Select an exact paired device or another fresh-file destination.
python airlift_windows.py --device <UDID> --target /var/mobile/Library/Caches
```

The Windows result uses the same top-level evidence fields as the macOS PoC:
exact recovered bytes, target/generated-path cleanup, and verified restoration
of the Books preimage. Unexpected or untested iPhone builds are allowed with a
warning, matching the upstream policy.

The Windows implementation was validated over USB against the default
`/var/mobile/Library/SpringBoard` target on:

- iPhone 15 Pro (`iPhone16,1`), iOS 27.0 final (`24A437`): exact 83-byte
  canary, all three expected manifest assets, complete generated-path cleanup,
  and restoration of the initially empty Books preimage; and
- iPhone 17 Pro (`iPhone18,1`), iOS 26.0 (`23A330`): exact 83-byte canary,
  complete cleanup, and byte-for-byte restoration of five pre-existing Books
  artifacts.

The iOS 26 result is a compatibility observation, not a claim that iOS 26 is
an upstream tested target.
