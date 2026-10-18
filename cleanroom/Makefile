CLANG := xcrun clang
MOBILEDEVICE := /System/Library/PrivateFrameworks/MobileDevice.framework/MobileDevice
AIRTRAFFIC := /System/Library/PrivateFrameworks/AirTrafficHost.framework/AirTrafficHost

.PHONY: all clean
all: host

host: host.m
	$(CLANG) -fobjc-arc -O2 -Wall -Wextra -framework Foundation $(MOBILEDEVICE) $(AIRTRAFFIC) $< -o $@
	codesign --force --sign - $@

clean:
	rm -f host
