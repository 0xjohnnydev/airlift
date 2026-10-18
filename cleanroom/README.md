# AirLift PoC

AirLift shows AirTraffic creating one new file outside the iPhone's Media
area. The file is a **canary**: short, random text used only to show where the
write landed. The PoC places it in `/var/tmp`, reads it back, and removes it.

## Before you run

Use a Mac with Python 3 and Xcode. Pair the iPhone with the Mac, connect it by
USB, and find its UDID (device identifier):

```sh
xcrun devicectl list devices
```

AirLift accepts iOS 27.0 build `24A437`. It has been used on an iPhone
18 Pro (`iPhone19,2`).

From the repository root, run:

```sh
make -C cleanroom
python3 cleanroom/poc.py --device YOUR_UDID
```

Replace `YOUR_UDID` with the identifier shown by the first command.

## What happens

1. The Mac makes a ZIP archive with the canary and a **symbolic link** (a
   file-system pointer). At first, the link points to another folder inside
   the extracted archive.
2. The Mac sends the archive to the iPhone and starts a Books media sync.
   AirTraffic moves the link to a new location, then follows it when it moves
   the canary. That places the canary in `/var/tmp`, outside Media.
3. AirTraffic moves the canary back into Media. The Mac reads it through
   **AFC** (Apple File Conduit, the service that gives a paired Mac access to
   Media) and compares every byte with the canary it created.
4. The PoC removes the files it created. It preserves unrelated Books content
   already on the phone and stops if a file it needs to write already exists.

The output uses `exactBytesRecovered: true` for the matching file contents
and `cleanupComplete: true` for completed cleanup. If cleanup fails, look for
`recoveryFiles` in the output; that directory holds the Mac-side inputs needed
to inspect the run. `fileCompleteCallsReturned: 3` means the Mac called the
private AirTraffic API three times. The API does not confirm delivery of each
call, so the file readback is the evidence of the effect.

The archive upload and AFC read use a direct device connection. The sync API
does not report whether it used USB or Wi-Fi.

The `cleanroom` folder name refers to a separate implementation. It does not
claim the vulnerability was discovered without studying earlier AirLift code.
