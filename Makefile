CLANG := xcrun clang
CFLAGS := -fobjc-arc -O2 -Wall -Wextra
FOUNDATION := -framework Foundation -framework CoreFoundation
MOBILEDEVICE := /System/Library/PrivateFrameworks/MobileDevice.framework/MobileDevice
AIRTRAFFIC := /System/Library/PrivateFrameworks/AirTrafficHost.framework/AirTrafficHost

.PHONY: all clean

all: build/device_helper build/airtraffic_host

build:
	mkdir -p $@

build/device_helper: Sources/device_helper.m Sources/airlift_target.h | build
	$(CLANG) $(CFLAGS) $(FOUNDATION) $(MOBILEDEVICE) $< -o $@
	codesign --force --sign - $@

build/airtraffic_host: Sources/airtraffic_host.m | build
	$(CLANG) $(CFLAGS) $(FOUNDATION) $(AIRTRAFFIC) $< -o $@
	codesign --force --sign - $@

clean:
	rm -rf build
