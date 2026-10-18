# Independent AirLift PoC

This directory contains a separate implementation of the AirLift path escape. It does not import or run the repository's original Python program or native helpers. It demonstrates one fresh-file write to `/var/tmp` on a paired iPhone over USB. The canary is moved back into Media, read through AFC, compared byte for byte, and removed.

Build and run on the paired Mac:

```sh
make -C cleanroom
python3 cleanroom/poc.py --device <iPhone UDID>
```

The PoC currently accepts iOS 27.0 build 24A437. It refuses to start if a Books file it needs to write already exists, or if its generated Media paths already exist. It preserves unrelated Books content, including existing directories. It checks and removes the files and directories it creates. If cleanup fails, it leaves its local recovery inputs in a temporary directory and reports that path.

The run is complete only when the phone returns the exact canary bytes and cleanup is verified. A successful compile, sync handshake, or printed success line alone is insufficient.
