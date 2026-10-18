# airlift

### An AirTraffic sandbox escape for iOS 27.0.

This is a proof of concept for developers and security researchers.

If you are worried about 🔥🪲4⃣☁️ (burning bugs for clout) - there are always
more bugs <sub><img src="./archive/original/assets/trollface.svg" width="22" height="18" alt="trollface"></sub>

AirTraffic syncs media, including Books, from a Mac to an iPhone. AirLift uses
that sync path to make one new file outside AirTraffic's intended Media area.
It reads the file back and removes it, without installing an iPhone app. Here,
“sandbox escape” means the media service crosses that file boundary; it does
not mean kernel access or control of the whole phone.

## Build and run

You need a Mac with Python 3 and Xcode, plus a paired iPhone that trusts the
Mac and is connected by USB. AirLift accepts iOS 27.0 build `24A437`. It has
been used on an iPhone 18 Pro (`iPhone19,2`). From the repository root:

```sh
xcrun devicectl list devices
make
python3 cleanroom/poc.py --device YOUR_UDID
```

The first command shows the phone's **UDID** (device identifier). Replace
`YOUR_UDID` with that value. The [PoC guide](cleanroom/README.md) has a shorter
step-by-step account of the run.

## Demonstrated scope

AirLift makes a fresh, randomly named canary file in `/var/tmp`:

```text
/var/tmp/cl-canary-<random>.bin
```

A **canary** is short, random text used to show where the write landed. The
PoC moves that file back into Media and reads it through **AFC** (Apple File
Conduit), which gives a paired Mac access to Media. It compares every byte
with the text it created. This run demonstrates that one file path; it does
not establish writes to other target directories or reads of unrelated files.

## Components

The Mac creates a ZIP archive and Books metadata, then uses two private Apple
frameworks to talk to the iPhone:

```text
macOS
  cleanroom/poc.py
    └─ cleanroom/host
         ├─ MobileDevice.framework → StreamingZip and AFC
         └─ AirTrafficHost.framework → Books sync

iOS
  com.apple.streaming_zip_conduit → unpacks the ZIP archive
  com.apple.afc                   → stages and reads files in Media
  com.apple.atc / AirTrafficDevice and Books sync client
                                  → process Book assets
  ATLegacyAssetLink → ATAirlock → NSFileManager
                                  → move the files
```

The ZIP upload and AFC read use a direct device connection. The Books sync
API does not report whether its connection used USB or Wi-Fi.

## Why the path escapes

For a Book asset, the Books “Persistent ID” supplies the source path and
`FileComplete.AssetPath` supplies the destination path. The relevant path
handling in `-[ATAirlock processCompletedAsset:]` can be summarized as:

```objc
NSString *source =
    [@"/var/mobile/Media/Airlock/Book"
        stringByAppendingPathComponent:asset.identifier];

NSString *destination =
    [[@"/var/mobile/Media/"
        stringByAppendingPathComponent:asset.path]
        stringByStandardizingPath];

if (![destination hasPrefix:@"/var/mobile/Media/"])
    return;

[fileManager moveItemAtPath:source
                     toPath:destination
                      error:&error];
```

The source can contain `..` (“parent directory”). The destination check looks
at the path string, not where a **symbolic link** (a file-system pointer)
actually leads. AirLift uses those two gaps in three file moves:

1. Its ZIP archive contains a relative symbolic link. While the link is in
   the extracted archive, it points to another folder inside that archive.
   The first Book move places the link under Media, where the same relative
   path now points to `/var/tmp`.
2. The second move sends the canary through that link. It lands at
   `/var/tmp/cl-canary-<random>.bin`, outside Media.
3. The third move brings the canary back into Media. AFC reads it there so
   the Mac can compare the bytes.

## Result and cleanup

The run prints JSON. `exactBytesRecovered: true` means the recovered file
matches the canary byte for byte. `cleanupComplete: true` means the PoC
removed its temporary files. A successful run reports both as true.

`sync.fileCompleteCallsReturned: 3` means the Mac's three AirTraffic API
calls returned. The API does not confirm delivery of each call; the file
readback is the evidence of the effect.

Before writing, AirLift checks for files that could conflict with its run.
It leaves unrelated Books content in place. If cleanup cannot finish, the
result names a `recoveryFiles` directory on the Mac with the inputs needed
to inspect the run.
